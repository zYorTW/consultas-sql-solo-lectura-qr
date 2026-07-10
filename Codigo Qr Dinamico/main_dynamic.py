import json
import logging
import os
import re
import sys
import threading
import tkinter as tk
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from logging.handlers import RotatingFileHandler
from tkinter import ttk, messagebox

import keyring
import pyodbc
import qrcode
from qrcode.exceptions import DataOverflowError
from PIL import Image, ImageTk
from dotenv import load_dotenv

# ==========================================
# CONFIGURACIÓN DE ENTORNO Y LOGGING
# ==========================================
# ponytail: anchor config/log paths to this script's own folder (not cwd) so
# behavior doesn't depend on how the app is launched (editor Run button,
# double-clicked .exe, or `python main_dynamic.py` from a different folder).
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

CONNECTIONS_FILE = os.getenv("CONNECTIONS_FILE", os.path.join(BASE_DIR, "db_connections.json"))
QUERIES_FILE = os.getenv("QUERIES_FILE", os.path.join(BASE_DIR, "queries_config.json"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()

try:
    MAX_ROWS = int(os.getenv("MAX_ROWS", "500"))
except ValueError:
    MAX_ROWS = 500

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        RotatingFileHandler(
            os.path.join(BASE_DIR, "app.log"), maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
    ],
)

PARAM_TYPES = ["str", "int", "float", "date"]
AUTH_TYPES = ["windows", "sql_server"]

# Servicio bajo el cual se guardan las contraseñas en el
# Administrador de credenciales de Windows (vía keyring).
KEYRING_SERVICE = "ConsultasDinamicasSQL"


class SQLSecurityError(Exception):
    pass


class ConfigError(Exception):
    pass


# ==========================================
# VALIDACIÓN DE SOLO LECTURA (bloqueante)
# ==========================================
_BLOCKED_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "MERGE", "EXEC", "EXECUTE", "USE", "GRANT", "REVOKE", "DENY",
    "BACKUP", "RESTORE", "PUT", "INTO",
    # Administración / DoS / exfiltración (no pedidos explícitamente, pero
    # violan el principio de solo lectura tanto como los anteriores).
    "SHUTDOWN", "KILL", "DBCC", "RECONFIGURE", "WAITFOR", "BULK",
    "OPENROWSET", "OPENQUERY", "OPENDATASOURCE",
]
_BLOCKED_PREFIXES = ["XP_", "SP_"]

_COMMENT_RE = re.compile(r"--.*?$|/\*.*?\*/", re.MULTILINE | re.DOTALL)
_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_TOP_RE = re.compile(r"SELECT\s+TOP\s*\(?\s*\d+", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)


def strip_sql_noise(sql):
    """Quita comentarios y literales de texto para que la validación no los confunda con SQL real."""
    no_comments = _COMMENT_RE.sub(" ", sql)
    return _STRING_RE.sub("''", no_comments)


def validate_readonly_sql(sql):
    """Lanza SQLSecurityError si la consulta no es un SELECT único de solo lectura."""
    if not sql or not sql.strip():
        raise SQLSecurityError("La consulta SQL no puede estar vacía.")

    cleaned = strip_sql_noise(sql).strip()

    statements = [s for s in cleaned.split(";") if s.strip()]
    if len(statements) > 1:
        raise SQLSecurityError("No se permiten múltiples sentencias SQL separadas por ';'.")
    if not statements:
        raise SQLSecurityError("La consulta SQL no puede estar vacía.")

    stripped = statements[0].strip()
    if not re.match(r"^SELECT\b", stripped, re.IGNORECASE):
        raise SQLSecurityError("Solo se permiten consultas que inicien con SELECT.")

    upper = stripped.upper()
    for kw in _BLOCKED_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            raise SQLSecurityError(f"La palabra clave '{kw}' no está permitida en consultas de solo lectura.")
    for prefix in _BLOCKED_PREFIXES:
        if re.search(rf"\b{prefix}\w*", upper):
            raise SQLSecurityError(f"No se permite invocar procedimientos con prefijo '{prefix}'.")

    return True


def needs_row_filter_warning(sql):
    return not _TOP_RE.search(sql) and not _WHERE_RE.search(sql)


def query_allowed_on(query, connection_name):
    """True si la consulta puede ejecutarse en la conexión dada.
    Lista 'allowed_connections' vacía o ausente = permitida en todas."""
    allowed = query.get("allowed_connections") or []
    return not allowed or connection_name in allowed


# ==========================================
# CONTRASEÑAS (Administrador de credenciales de Windows)
# ==========================================
def save_password(password_ref, password):
    keyring.set_password(KEYRING_SERVICE, password_ref, password)


def get_password(password_ref):
    if not password_ref:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, password_ref)
    except Exception:
        logging.exception("No se pudo leer la credencial '%s' del almacén de Windows", password_ref)
        return None


def delete_password(password_ref):
    if not password_ref:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, password_ref)
    except Exception:
        # La credencial puede no existir; no es un error fatal.
        logging.info("No se eliminó la credencial '%s' (posiblemente no existía)", password_ref)


# ==========================================
# ALMACENES JSON (conexiones y consultas)
# ==========================================
class JsonListStore:
    """Lista de dicts identificados por 'name', persistida en un archivo JSON local."""

    def __init__(self, path):
        self.path = path
        self.items = []
        self.load_error = None
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            self.save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.items = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.exception("Error leyendo %s", self.path)
            self.items = []
            self.load_error = f"No se pudo leer '{self.path}': {e}"

    def save(self):
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    def active_items(self):
        return [i for i in self.items if i.get("active", True)]

    def find_by_name(self, name):
        return next((i for i in self.items if i["name"] == name), None)

    def add(self, item):
        self._validate(item)
        if self.find_by_name(item["name"]):
            raise ConfigError(f"Ya existe un elemento llamado '{item['name']}'.")
        self.items.append(item)
        self.save()

    def update(self, original_name, item):
        self._validate(item)
        idx = next((i for i, it in enumerate(self.items) if it["name"] == original_name), None)
        if idx is None:
            raise ConfigError("El elemento a editar ya no existe.")
        if item["name"] != original_name and self.find_by_name(item["name"]):
            raise ConfigError(f"Ya existe un elemento llamado '{item['name']}'.")
        self.items[idx] = item
        self.save()

    def delete(self, name):
        self.items = [i for i in self.items if i["name"] != name]
        self.save()

    def _validate(self, item):
        raise NotImplementedError


class DatabaseConnectionStore(JsonListStore):
    def _validate(self, conn):
        if not conn.get("name", "").strip():
            raise ConfigError("El nombre de la conexión es obligatorio.")
        if not conn.get("driver", "").strip():
            raise ConfigError("Debes indicar el driver ODBC.")
        if not conn.get("server", "").strip():
            raise ConfigError("Debes indicar el servidor.")
        if not conn.get("database", "").strip():
            raise ConfigError("Debes indicar la base de datos.")
        if conn.get("auth_type") not in AUTH_TYPES:
            raise ConfigError("El tipo de autenticación debe ser 'windows' o 'sql_server'.")
        if conn["auth_type"] == "sql_server" and not conn.get("username", "").strip():
            raise ConfigError("Debes indicar el usuario para autenticación SQL Server.")
        if not isinstance(conn.get("timeout"), int) or conn["timeout"] < 1:
            raise ConfigError("El timeout debe ser un número entero de segundos (mínimo 1).")

    def delete(self, name):
        conn = self.find_by_name(name)
        if conn and conn.get("password_ref"):
            delete_password(conn["password_ref"])
        super().delete(name)


class QueryConfigStore(JsonListStore):
    def _validate(self, query):
        if not query.get("name", "").strip():
            raise ConfigError("El nombre de la consulta es obligatorio.")
        if not query.get("sql", "").strip():
            raise ConfigError("La sentencia SQL es obligatoria.")

        validate_readonly_sql(query["sql"])

        params = query.get("params", [])
        for p in params:
            if not p.get("name", "").strip():
                raise ConfigError("Cada parámetro debe tener un nombre.")
            if p.get("type") not in PARAM_TYPES:
                raise ConfigError(f"Tipo de parámetro inválido: {p.get('type')}")

        placeholder_count = strip_sql_noise(query["sql"]).count("?")
        if placeholder_count != len(params):
            raise ConfigError(
                f"La consulta tiene {placeholder_count} parámetro(s) '?' pero se definieron "
                f"{len(params)}. Deben coincidir en cantidad y orden."
            )

        allowed = query.get("allowed_connections", [])
        if not isinstance(allowed, list) or any(not isinstance(n, str) for n in allowed):
            raise ConfigError("'allowed_connections' debe ser una lista de nombres de conexión.")


def convert_param_value(raw_value, param):
    raw_value = (raw_value or "").strip()
    label = param.get("label") or param.get("name")

    if not raw_value:
        if param.get("required", True):
            raise ValueError(f"El parámetro '{label}' es obligatorio.")
        return None

    ptype = param.get("type", "str")
    try:
        if ptype == "int":
            return int(raw_value)
        if ptype == "float":
            return float(raw_value)
        if ptype == "date":
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        return raw_value
    except ValueError:
        hint = " (formato AAAA-MM-DD)" if ptype == "date" else ""
        raise ValueError(f"El parámetro '{label}' debe ser de tipo {ptype}{hint}.")


# ==========================================
# ACCESO A DATOS
# ==========================================
def build_connection_string(conn_cfg, password=None):
    """Arma el connection string de pyodbc para una conexión guardada.
    'password' permite probar con una contraseña recién digitada sin guardarla."""
    name = conn_cfg.get("name", "?")
    driver = conn_cfg.get("driver", "").strip()
    server = conn_cfg.get("server", "").strip()
    database = conn_cfg.get("database", "").strip()

    if not driver or not server or not database:
        raise ValueError(f"La conexión '{name}' está incompleta (driver, servidor o base de datos).")

    base = (
        f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
        f"Encrypt=yes;TrustServerCertificate=yes;"
    )

    if conn_cfg.get("auth_type") == "windows":
        return base + "Trusted_Connection=yes;"

    username = conn_cfg.get("username", "").strip()
    pwd = password or get_password(conn_cfg.get("password_ref", ""))

    if not username:
        raise ValueError(f"La conexión '{name}' no tiene usuario configurado.")
    if not pwd:
        raise ValueError(
            f"La conexión '{name}' no tiene contraseña guardada en el almacén de credenciales. "
            "Edita la conexión y vuelve a ingresar la contraseña."
        )

    return base + f"UID={username};PWD={pwd};"


def test_connection(conn_cfg, password=None):
    """Abre la conexión y ejecuta SELECT 1. Lanza excepción si algo falla."""
    conn = pyodbc.connect(
        build_connection_string(conn_cfg, password),
        timeout=conn_cfg.get("timeout", 5),
    )
    try:
        conn.cursor().execute("SELECT 1").fetchone()
    finally:
        conn.close()


def normalize_value(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return str(value)
    return value


def fetch_query_data(conn_cfg, sql, params):
    conn = pyodbc.connect(
        build_connection_string(conn_cfg),
        timeout=conn_cfg.get("timeout", 5),
    )
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        columns = [col[0] for col in cursor.description] if cursor.description else []

        raw_rows = cursor.fetchmany(MAX_ROWS + 1)
        truncated = len(raw_rows) > MAX_ROWS
        raw_rows = raw_rows[:MAX_ROWS]

        rows = [
            {col: normalize_value(row[idx]) for idx, col in enumerate(columns)}
            for row in raw_rows
        ]
        return rows, truncated
    finally:
        conn.close()


def generate_qr_image(text_data):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(text_data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


# ==========================================
# DIÁLOGO: CREAR / EDITAR CONEXIÓN
# ==========================================
class ConnectionEditorDialog(tk.Toplevel):
    def __init__(self, parent, store, existing=None, on_saved=None):
        super().__init__(parent)
        self.store = store
        self.existing = existing
        self.on_saved = on_saved
        self.original_name = existing["name"] if existing else None
        self.is_testing = False

        self.title("Editar conexión" if existing else "Nueva conexión")
        self.geometry("600x440")
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        if existing:
            self._load_existing()
        self._sync_auth_fields()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Nombre:").grid(row=0, column=0, sticky="w", **pad)
        self.name_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.name_var, width=45).grid(row=0, column=1, columnspan=2, sticky="we", **pad)

        ttk.Label(frm, text="Driver ODBC:").grid(row=1, column=0, sticky="w", **pad)
        self.driver_var = tk.StringVar(value="ODBC Driver 17 for SQL Server")
        ttk.Entry(frm, textvariable=self.driver_var, width=45).grid(row=1, column=1, columnspan=2, sticky="we", **pad)

        ttk.Label(frm, text="Servidor:").grid(row=2, column=0, sticky="w", **pad)
        self.server_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.server_var, width=45).grid(row=2, column=1, columnspan=2, sticky="we", **pad)

        ttk.Label(frm, text="Base de datos:").grid(row=3, column=0, sticky="w", **pad)
        self.database_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.database_var, width=45).grid(row=3, column=1, columnspan=2, sticky="we", **pad)

        ttk.Label(frm, text="Autenticación:").grid(row=4, column=0, sticky="w", **pad)
        self.auth_var = tk.StringVar(value="sql_server")
        ttk.Radiobutton(
            frm, text="Usuario SQL Server", value="sql_server",
            variable=self.auth_var, command=self._sync_auth_fields
        ).grid(row=4, column=1, sticky="w", **pad)
        ttk.Radiobutton(
            frm, text="Autenticación de Windows", value="windows",
            variable=self.auth_var, command=self._sync_auth_fields
        ).grid(row=4, column=2, sticky="w", **pad)

        ttk.Label(frm, text="Usuario:").grid(row=5, column=0, sticky="w", **pad)
        self.user_var = tk.StringVar()
        self.user_entry = ttk.Entry(frm, textvariable=self.user_var, width=30)
        self.user_entry.grid(row=5, column=1, sticky="w", **pad)

        ttk.Label(frm, text="Contraseña:").grid(row=6, column=0, sticky="w", **pad)
        # Nunca se muestra la contraseña guardada: el campo siempre inicia vacío
        # y en edición dejarlo vacío significa "conservar la actual".
        self.password_entry = ttk.Entry(frm, show="*", width=30)
        self.password_entry.grid(row=6, column=1, sticky="w", **pad)
        hint = "(dejar vacío para conservar la actual)" if self.existing else ""
        self.password_hint = ttk.Label(frm, text=hint, foreground="#555")
        self.password_hint.grid(row=6, column=2, sticky="w", **pad)

        ttk.Label(frm, text="Timeout (segundos):").grid(row=7, column=0, sticky="w", **pad)
        self.timeout_var = tk.StringVar(value="5")
        ttk.Entry(frm, textvariable=self.timeout_var, width=8).grid(row=7, column=1, sticky="w", **pad)

        self.active_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Conexión activa", variable=self.active_var).grid(
            row=8, column=0, columnspan=2, sticky="w", **pad
        )

        frm.columnconfigure(1, weight=1)

        btns = ttk.Frame(self, padding=10)
        btns.pack(fill="x", side="bottom")
        self.test_button = ttk.Button(btns, text="Probar conexión", command=self.on_test)
        self.test_button.pack(side="left", padx=4)
        ttk.Button(btns, text="Guardar", command=self.on_save).pack(side="right", padx=4)
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side="right", padx=4)

    def _sync_auth_fields(self):
        state = "normal" if self.auth_var.get() == "sql_server" else "disabled"
        self.user_entry.config(state=state)
        self.password_entry.config(state=state)

    def _load_existing(self):
        self.name_var.set(self.existing["name"])
        self.driver_var.set(self.existing.get("driver", ""))
        self.server_var.set(self.existing.get("server", ""))
        self.database_var.set(self.existing.get("database", ""))
        self.auth_var.set(self.existing.get("auth_type", "sql_server"))
        self.user_var.set(self.existing.get("username", ""))
        self.timeout_var.set(str(self.existing.get("timeout", 5)))
        self.active_var.set(self.existing.get("active", True))

    def _collect_config(self):
        try:
            timeout = int(self.timeout_var.get().strip() or "5")
        except ValueError:
            raise ConfigError("El timeout debe ser un número entero de segundos.")

        return {
            "name": self.name_var.get().strip(),
            "driver": self.driver_var.get().strip(),
            "server": self.server_var.get().strip(),
            "database": self.database_var.get().strip(),
            "auth_type": self.auth_var.get(),
            "username": self.user_var.get().strip(),
            "password_ref": (self.existing or {}).get("password_ref", ""),
            "timeout": timeout,
            "active": self.active_var.get(),
        }

    def on_test(self):
        if self.is_testing:
            return
        try:
            cfg = self._collect_config()
            self.store._validate(cfg)
        except ConfigError as e:
            messagebox.showerror("Datos incompletos", str(e), parent=self)
            return

        typed = self.password_entry.get() or None
        if cfg["auth_type"] == "sql_server" and not typed and not get_password(cfg["password_ref"]):
            messagebox.showerror(
                "Falta la contraseña",
                "Ingresa la contraseña para poder probar la conexión.",
                parent=self,
            )
            return

        self.is_testing = True
        self.test_button.config(state="disabled", text="Probando...")
        threading.Thread(target=self._test_thread, args=(cfg, typed), daemon=True).start()

    def _test_thread(self, cfg, password):
        try:
            test_connection(cfg, password)
            error = None
        except Exception as e:
            logging.exception("Prueba de conexión fallida ('%s')", cfg.get("name"))
            error = str(e)
        self.after(0, lambda: self._test_done(error))

    def _test_done(self, error):
        if not self.winfo_exists():
            return
        self.is_testing = False
        self.test_button.config(state="normal", text="Probar conexión")
        if error is None:
            messagebox.showinfo("Prueba de conexión", "Conexión exitosa.", parent=self)
        else:
            messagebox.showerror(
                "Prueba de conexión", f"No se pudo conectar:\n{error[:400]}", parent=self
            )

    def on_save(self):
        try:
            cfg = self._collect_config()
        except ConfigError as e:
            messagebox.showerror("No se puede guardar", str(e), parent=self)
            return

        typed = self.password_entry.get()

        if cfg["auth_type"] == "sql_server":
            ref = cfg["password_ref"] or uuid.uuid4().hex
            if not typed and not get_password(ref):
                messagebox.showerror(
                    "No se puede guardar",
                    "Debes ingresar la contraseña para esta conexión.",
                    parent=self,
                )
                return
            cfg["password_ref"] = ref
        else:
            # Al pasar a autenticación de Windows, limpiar la credencial almacenada.
            if cfg["password_ref"]:
                delete_password(cfg["password_ref"])
            cfg["password_ref"] = ""

        try:
            if self.original_name:
                self.store.update(self.original_name, cfg)
            else:
                self.store.add(cfg)
        except ConfigError as e:
            messagebox.showerror("No se puede guardar", str(e), parent=self)
            return

        if cfg["auth_type"] == "sql_server" and typed:
            try:
                save_password(cfg["password_ref"], typed)
            except Exception:
                logging.exception("No se pudo guardar la contraseña en el almacén de Windows")
                messagebox.showerror(
                    "Contraseña no guardada",
                    "La conexión se guardó, pero no se pudo guardar la contraseña en el "
                    "Administrador de credenciales de Windows. Edita la conexión e inténtalo de nuevo.",
                    parent=self,
                )

        if self.on_saved:
            self.on_saved(cfg["name"])
        self.destroy()


# ==========================================
# DIÁLOGO: CREAR / EDITAR CONSULTA
# ==========================================
class QueryEditorDialog(tk.Toplevel):
    def __init__(self, parent, store, connection_names=None, existing=None, on_saved=None):
        super().__init__(parent)
        self.store = store
        self.connection_names = connection_names or []
        self.existing = existing
        self.on_saved = on_saved
        self.original_name = existing["name"] if existing else None
        self.param_rows = []

        self.title("Editar consulta" if existing else "Nueva consulta")
        self.geometry("720x700")
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        if existing:
            self._load_existing()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Nombre:").grid(row=0, column=0, sticky="w", **pad)
        self.name_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.name_var, width=50).grid(row=0, column=1, sticky="we", **pad)

        ttk.Label(top, text="Descripción:").grid(row=1, column=0, sticky="w", **pad)
        self.desc_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.desc_var, width=50).grid(row=1, column=1, sticky="we", **pad)

        self.generate_qr_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Generar QR con el resultado", variable=self.generate_qr_var).grid(
            row=2, column=0, columnspan=2, sticky="w", **pad
        )

        self.active_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Consulta activa", variable=self.active_var).grid(
            row=3, column=0, columnspan=2, sticky="w", **pad
        )

        top.columnconfigure(1, weight=1)

        sql_frame = ttk.LabelFrame(self, text="Sentencia SQL (solo SELECT, sin ';' múltiples)", padding=8)
        sql_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.sql_text = tk.Text(sql_frame, height=8, wrap="word")
        self.sql_text.pack(fill="both", expand=True)

        allowed_frame = ttk.LabelFrame(
            self, text="Conexiones permitidas (ninguna marcada = todas)", padding=8
        )
        allowed_frame.pack(fill="x", padx=10, pady=(0, 8))

        self.allowed_vars = []
        for i, cname in enumerate(self.connection_names):
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(allowed_frame, text=cname, variable=var).grid(
                row=i // 3, column=i % 3, sticky="w", padx=4, pady=2
            )
            self.allowed_vars.append((cname, var))
        if not self.connection_names:
            ttk.Label(allowed_frame, text="No hay conexiones definidas todavía.").pack(anchor="w")

        params_frame = ttk.LabelFrame(
            self, text="Parámetros (mismo orden que los '?' de la consulta)", padding=8
        )
        params_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self.params_container = ttk.Frame(params_frame)
        self.params_container.pack(fill="both", expand=True)

        ttk.Button(params_frame, text="+ Agregar parámetro", command=self.add_param_row).pack(
            anchor="w", pady=(6, 0)
        )

        btns = ttk.Frame(self, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="Guardar", command=self.on_save).pack(side="right", padx=4)
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side="right", padx=4)

    def add_param_row(self, param=None):
        row_frame = ttk.Frame(self.params_container)
        row_frame.pack(fill="x", pady=2)

        name_var = tk.StringVar(value=(param or {}).get("name", ""))
        label_var = tk.StringVar(value=(param or {}).get("label", ""))
        type_var = tk.StringVar(value=(param or {}).get("type", "str"))
        required_var = tk.BooleanVar(value=(param or {}).get("required", True))

        ttk.Entry(row_frame, textvariable=name_var, width=16).pack(side="left", padx=2)
        ttk.Entry(row_frame, textvariable=label_var, width=22).pack(side="left", padx=2)
        ttk.Combobox(
            row_frame, textvariable=type_var, values=PARAM_TYPES, width=8, state="readonly"
        ).pack(side="left", padx=2)
        ttk.Checkbutton(row_frame, text="Requerido", variable=required_var).pack(side="left", padx=2)

        row = {"frame": row_frame, "name": name_var, "label": label_var, "type": type_var, "required": required_var}

        def remove():
            row_frame.destroy()
            self.param_rows.remove(row)

        ttk.Button(row_frame, text="Quitar", command=remove).pack(side="left", padx=2)

        self.param_rows.append(row)

    def _load_existing(self):
        self.name_var.set(self.existing["name"])
        self.desc_var.set(self.existing.get("description", ""))
        self.sql_text.insert("1.0", self.existing.get("sql", ""))
        self.generate_qr_var.set(self.existing.get("generate_qr", True))
        self.active_var.set(self.existing.get("active", True))
        existing_allowed = self.existing.get("allowed_connections") or []
        for cname, var in self.allowed_vars:
            var.set(cname in existing_allowed)
        for p in self.existing.get("params", []):
            self.add_param_row(p)

    def on_save(self):
        checked = [cname for cname, var in self.allowed_vars if var.get()]
        # Conservar nombres permitidos que apunten a conexiones hoy inexistentes
        # (p. ej. definidas en otro equipo) en vez de perderlos silenciosamente.
        known = [cname for cname, _ in self.allowed_vars]
        existing_allowed = (self.existing or {}).get("allowed_connections") or []
        allowed = checked + [n for n in existing_allowed if n not in known]

        query = {
            "name": self.name_var.get().strip(),
            "description": self.desc_var.get().strip(),
            "sql": self.sql_text.get("1.0", "end").strip(),
            "generate_qr": self.generate_qr_var.get(),
            "active": self.active_var.get(),
            "allowed_connections": allowed,
            "params": [
                {
                    "name": r["name"].get().strip(),
                    "label": r["label"].get().strip() or r["name"].get().strip(),
                    "type": r["type"].get(),
                    "required": r["required"].get(),
                }
                for r in self.param_rows
            ],
        }

        try:
            if self.original_name:
                self.store.update(self.original_name, query)
            else:
                self.store.add(query)
        except (ConfigError, SQLSecurityError) as e:
            messagebox.showerror("No se puede guardar", str(e), parent=self)
            return

        if self.on_saved:
            self.on_saved(query["name"])
        self.destroy()


class QrDisplayDialog(tk.Toplevel):
    def __init__(self, parent, image_pil):
        super().__init__(parent)
        self.image_pil = image_pil
        self.photo = None

        self.title("Código QR")
        self.geometry("420x460")
        self.minsize(280, 320)
        self.transient(parent)
        self.grab_set()

        self.qr_label = ttk.Label(self, anchor="center")
        self.qr_label.pack(fill="both", expand=True, padx=15, pady=15)
        ttk.Button(self, text="Cerrar", command=self.destroy).pack(pady=(0, 15))

        self.bind("<Configure>", lambda e: self._render())
        self.after(10, self._render)

    def _render(self):
        self.update_idletasks()
        size = min(self.qr_label.winfo_width(), self.qr_label.winfo_height())
        size = max(150, min(size, 600))
        img_resized = self.image_pil.copy().resize((size, size), Image.Resampling.NEAREST)
        self.photo = ImageTk.PhotoImage(img_resized)
        self.qr_label.config(image=self.photo)


# ==========================================
# APLICACIÓN PRINCIPAL
# ==========================================
class DynamicQueryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Consultas Dinámicas SQL (solo lectura)")
        # ponytail: cap la altura inicial a la pantalla real, así la ventana no queda
        # cortada en portátiles con menos de 900px de alto disponibles.
        screen_height = self.root.winfo_screenheight()
        window_height = min(900, screen_height - 100)
        self.root.geometry(f"1000x{window_height}")
        self.root.minsize(800, min(620, window_height))

        self.last_rows = None
        self.last_query = None
        self.is_query_running = False
        self.param_widgets = []

        self.conn_store = DatabaseConnectionStore(CONNECTIONS_FILE)
        self.store = QueryConfigStore(QUERIES_FILE)
        for s in (self.conn_store, self.store):
            if s.load_error:
                messagebox.showerror("Error de configuración", s.load_error)

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_connection_list(auto_select=False)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        conn_frame = ttk.LabelFrame(main, text="Conexión / base de datos", padding=10)
        conn_frame.pack(fill="x", pady=(0, 10))

        self.conn_var = tk.StringVar()
        self.conn_combo = ttk.Combobox(
            conn_frame, textvariable=self.conn_var, state="readonly", width=40
        )
        self.conn_combo.grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.conn_combo.bind("<<ComboboxSelected>>", lambda e: self.on_connection_selected(auto_select=False))

        new_conn_btn = ttk.Button(conn_frame, text="Nueva conexión", command=self.new_connection)
        new_conn_btn.grid(row=0, column=1, padx=5)
        edit_conn_btn = ttk.Button(conn_frame, text="Editar conexión", command=self.edit_connection)
        edit_conn_btn.grid(row=0, column=2, padx=5)
        del_conn_btn = ttk.Button(conn_frame, text="Eliminar conexión", command=self.delete_connection)
        del_conn_btn.grid(row=0, column=3, padx=5)
        test_conn_btn = ttk.Button(conn_frame, text="Probar conexión", command=self.test_selected_connection)
        test_conn_btn.grid(row=0, column=4, padx=5)

        self.conn_info_var = tk.StringVar()
        ttk.Label(conn_frame, textvariable=self.conn_info_var, foreground="#555").grid(
            row=1, column=0, columnspan=5, sticky="w", padx=5
        )

        selector_frame = ttk.LabelFrame(main, text="Consulta guardada", padding=10)
        selector_frame.pack(fill="x", pady=(0, 10))

        self.query_var = tk.StringVar()
        self.query_combo = ttk.Combobox(
            selector_frame, textvariable=self.query_var, state="readonly", width=45
        )
        self.query_combo.grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.query_combo.bind("<<ComboboxSelected>>", lambda e: self.on_query_selected())

        new_query_btn = ttk.Button(selector_frame, text="Nueva consulta", command=self.new_query)
        new_query_btn.grid(row=0, column=1, padx=5)
        edit_query_btn = ttk.Button(selector_frame, text="Editar consulta", command=self.edit_query)
        edit_query_btn.grid(row=0, column=2, padx=5)
        del_query_btn = ttk.Button(selector_frame, text="Eliminar consulta", command=self.delete_query)
        del_query_btn.grid(row=0, column=3, padx=5)

        self.desc_var = tk.StringVar()
        ttk.Label(selector_frame, textvariable=self.desc_var, foreground="#555").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=5
        )

        preview_frame = ttk.LabelFrame(main, text="Vista previa SQL", padding=8)
        preview_frame.pack(fill="x", pady=(0, 10))
        self.sql_preview = tk.Text(preview_frame, height=5, wrap="word", state="disabled")
        self.sql_preview.pack(fill="both", expand=True)

        self.params_frame = ttk.LabelFrame(main, text="Parámetros", padding=8)
        self.params_frame.pack(fill="x", pady=(0, 10))

        action_frame = ttk.Frame(main)
        action_frame.pack(fill="x", pady=(0, 10))
        self.run_button = ttk.Button(action_frame, text="Ejecutar consulta", command=self.run_query)
        self.run_button.pack(side="left", padx=5)
        self.clear_button = ttk.Button(action_frame, text="Limpiar", command=self.clear_all)
        self.clear_button.pack(side="left", padx=5)
        self.qr_button = ttk.Button(action_frame, text="Generar QR", command=self.generate_qr_from_result)
        self.qr_button.pack(side="left", padx=5)
        self.qr_button.config(state="disabled")

        self.status_var = tk.StringVar(value="Listo")
        ttk.Label(action_frame, textvariable=self.status_var).pack(side="left", padx=15)

        # Todos los botones que se bloquean mientras hay una operación en curso.
        self.action_buttons = [
            new_conn_btn, edit_conn_btn, del_conn_btn, test_conn_btn,
            new_query_btn, edit_query_btn, del_query_btn,
            self.run_button, self.clear_button, self.qr_button,
        ]

        result_frame = ttk.LabelFrame(main, text="Resultado JSON", padding=10)
        result_frame.pack(fill="both", expand=True, pady=(0, 10))
        text_container = ttk.Frame(result_frame)
        text_container.pack(fill="both", expand=True)
        self.result_text = tk.Text(text_container, height=6, wrap="word", state="disabled")
        result_scroll = ttk.Scrollbar(text_container, orient="vertical", command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=result_scroll.set)
        self.result_text.pack(side="left", fill="both", expand=True)
        result_scroll.pack(side="right", fill="y")

    # --- gestión de conexiones ---
    def refresh_connection_list(self, select_name=None, auto_select=True):
        names = [c["name"] for c in self.conn_store.active_items()]
        self.conn_combo["values"] = names
        current = self.conn_var.get()
        if select_name and select_name in names:
            self.conn_var.set(select_name)
        elif current in names:
            pass  # conservar la selección actual
        elif names and auto_select:
            self.conn_var.set(names[0])
        else:
            self.conn_var.set("")
        self.on_connection_selected(auto_select=auto_select)

    def get_selected_connection(self):
        name = self.conn_var.get()
        return self.conn_store.find_by_name(name) if name else None

    def on_connection_selected(self, auto_select=True):
        conn = self.get_selected_connection()
        if conn:
            auth = "Windows" if conn.get("auth_type") == "windows" else f"SQL ({conn.get('username', '')})"
            self.conn_info_var.set(
                f"{conn.get('server', '')}  /  {conn.get('database', '')}  —  Autenticación: {auth}"
            )
        elif not self.conn_store.active_items():
            self.conn_info_var.set("No hay conexiones. Crea una con 'Nueva conexión'.")
        else:
            self.conn_info_var.set("")
        self.refresh_query_list(select_name=self.query_var.get() or None, auto_select=auto_select)

    def new_connection(self):
        ConnectionEditorDialog(
            self.root, self.conn_store,
            on_saved=lambda name: self.refresh_connection_list(select_name=name),
        )

    def edit_connection(self):
        conn = self.get_selected_connection()
        if not conn:
            messagebox.showinfo("Editar conexión", "Selecciona una conexión primero.")
            return
        ConnectionEditorDialog(
            self.root, self.conn_store, existing=conn,
            on_saved=lambda name: self.refresh_connection_list(select_name=name),
        )

    def delete_connection(self):
        conn = self.get_selected_connection()
        if not conn:
            messagebox.showinfo("Eliminar conexión", "Selecciona una conexión primero.")
            return
        if messagebox.askyesno(
            "Eliminar conexión",
            f"¿Eliminar la conexión '{conn['name']}'?\n"
            "También se eliminará su contraseña guardada.",
        ):
            self.conn_store.delete(conn["name"])
            self.refresh_connection_list()

    def test_selected_connection(self):
        if self.is_query_running:
            return
        conn = self.get_selected_connection()
        if not conn:
            messagebox.showinfo("Probar conexión", "Selecciona una conexión primero.")
            return
        self.set_loading_state(True, "Probando conexión...")
        threading.Thread(target=self._test_connection_thread, args=(conn,), daemon=True).start()

    def _test_connection_thread(self, conn_cfg):
        try:
            test_connection(conn_cfg)
            error = None
        except Exception as e:
            logging.exception("Prueba de conexión fallida ('%s')", conn_cfg.get("name"))
            error = str(e)
        self.root.after(0, lambda: self._test_connection_done(conn_cfg["name"], error))

    def _test_connection_done(self, name, error):
        self.set_loading_state(False, "Listo")
        if error is None:
            messagebox.showinfo("Probar conexión", f"Conexión exitosa a '{name}'.")
        else:
            messagebox.showerror("Probar conexión", f"No se pudo conectar:\n{error[:400]}")

    # --- gestión de la lista de consultas ---
    def refresh_query_list(self, select_name=None, auto_select=True):
        conn_name = self.conn_var.get()
        queries = self.store.active_items()
        if conn_name:
            queries = [q for q in queries if query_allowed_on(q, conn_name)]
        names = [q["name"] for q in queries]
        self.query_combo["values"] = names
        if select_name and select_name in names:
            self.query_var.set(select_name)
        elif names and auto_select:
            self.query_var.set(names[0])
        else:
            self.query_var.set("")
        self.on_query_selected()

    def get_selected_query(self):
        name = self.query_var.get()
        return self.store.find_by_name(name) if name else None

    def on_query_selected(self):
        query = self.get_selected_query()
        self.clear_result_and_qr()
        self.clear_param_widgets()

        self.sql_preview.config(state="normal")
        self.sql_preview.delete("1.0", "end")

        if not query:
            self.desc_var.set("")
            self.sql_preview.config(state="disabled")
            return

        self.desc_var.set(query.get("description", ""))
        self.sql_preview.insert("1.0", query.get("sql", ""))
        self.sql_preview.config(state="disabled")

        self.build_param_widgets(query.get("params", []))

    def clear_param_widgets(self):
        for widget in self.params_frame.winfo_children():
            widget.destroy()
        self.param_widgets = []

    def build_param_widgets(self, params):
        for i, param in enumerate(params):
            ttk.Label(self.params_frame, text=f"{param['label']}:").grid(
                row=i, column=0, sticky="w", padx=5, pady=3
            )
            var = tk.StringVar()
            ttk.Entry(self.params_frame, textvariable=var, width=30).grid(
                row=i, column=1, sticky="w", padx=5, pady=3
            )
            self.param_widgets.append((param, var))

    # --- CRUD de consultas ---
    def _connection_names(self):
        return [c["name"] for c in self.conn_store.items]

    def new_query(self):
        QueryEditorDialog(
            self.root, self.store, connection_names=self._connection_names(),
            on_saved=lambda name: self.refresh_query_list(select_name=name),
        )

    def edit_query(self):
        query = self.get_selected_query()
        if not query:
            messagebox.showinfo("Editar consulta", "Selecciona una consulta primero.")
            return
        QueryEditorDialog(
            self.root, self.store, connection_names=self._connection_names(), existing=query,
            on_saved=lambda name: self.refresh_query_list(select_name=name),
        )

    def delete_query(self):
        query = self.get_selected_query()
        if not query:
            messagebox.showinfo("Eliminar consulta", "Selecciona una consulta primero.")
            return
        if messagebox.askyesno("Eliminar consulta", f"¿Eliminar la consulta '{query['name']}'?"):
            self.store.delete(query["name"])
            self.refresh_query_list()

    # --- ejecución ---
    def on_close(self):
        self.root.destroy()

    def set_loading_state(self, is_loading, message="Listo"):
        self.is_query_running = is_loading
        state = "disabled" if is_loading else "normal"
        for btn in self.action_buttons:
            btn.config(state=state)
        if not is_loading:
            self.refresh_qr_button_state()
        combo_state = "disabled" if is_loading else "readonly"
        self.query_combo.config(state=combo_state)
        self.conn_combo.config(state=combo_state)
        self.status_var.set(message)

    def refresh_qr_button_state(self):
        can_generate = bool(self.last_query and self.last_query.get("generate_qr") and self.last_rows)
        self.qr_button.config(state="normal" if can_generate else "disabled")

    def clear_result_and_qr(self):
        self.update_result_text("")
        self.last_rows = None
        self.last_query = None
        self.qr_button.config(state="disabled")

    def update_result_text(self, content):
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", content)
        self.result_text.config(state="disabled")

    def clear_all(self):
        if self.is_query_running:
            return
        self.clear_result_and_qr()
        self.status_var.set("Listo")

    def run_query(self):
        if self.is_query_running:
            return

        conn_cfg = self.get_selected_connection()
        if not conn_cfg:
            messagebox.showinfo("Ejecutar consulta", "Selecciona una conexión primero.")
            return

        query = self.get_selected_query()
        if not query:
            messagebox.showinfo("Ejecutar consulta", "Selecciona una consulta primero.")
            return

        if not query_allowed_on(query, conn_cfg["name"]):
            messagebox.showerror(
                "Consulta no permitida",
                f"La consulta '{query['name']}' no está permitida en la conexión '{conn_cfg['name']}'.",
            )
            return

        try:
            validate_readonly_sql(query["sql"])
        except SQLSecurityError as e:
            logging.warning("Consulta bloqueada por seguridad: %s", e)
            messagebox.showerror("Consulta bloqueada", str(e))
            return

        try:
            params_values = [
                convert_param_value(var.get(), param) for param, var in self.param_widgets
            ]
        except ValueError as e:
            messagebox.showerror("Parámetro inválido", str(e))
            return

        if needs_row_filter_warning(query["sql"]):
            proceed = messagebox.askyesno(
                "Consulta sin filtro",
                "Esta consulta no tiene TOP ni WHERE y podría devolver muchas filas.\n"
                f"El resultado se limitará a {MAX_ROWS} filas. ¿Deseas continuar?"
            )
            if not proceed:
                return

        self.set_loading_state(True, "Consultando...")
        thread = threading.Thread(
            target=self._run_query_thread, args=(conn_cfg, query, params_values), daemon=True
        )
        thread.start()

    def _run_query_thread(self, conn_cfg, query, params_values):
        try:
            logging.info("Ejecutando consulta '%s' en conexión '%s'", query["name"], conn_cfg["name"])
            rows, truncated = fetch_query_data(conn_cfg, query["sql"], params_values)
            self.root.after(0, lambda: self.handle_query_success(query, rows, truncated))

        except pyodbc.InterfaceError:
            logging.exception("No se pudo conectar al servidor SQL")
            self.root.after(0, lambda: self.handle_query_error("No se pudo conectar al servidor SQL."))

        except pyodbc.DatabaseError:
            logging.exception("Error ejecutando la consulta SQL")
            self.root.after(
                0, lambda: self.handle_query_error("Error ejecutando la consulta en la base de datos.")
            )

        except ValueError as e:
            logging.exception("Error de validación/configuración")
            self.root.after(0, lambda: self.handle_query_error(str(e)))

        except Exception:
            logging.exception("Error inesperado")
            self.root.after(0, lambda: self.handle_query_error("Se presentó un error inesperado."))

    def handle_query_success(self, query, rows, truncated):
        try:
            if rows:
                pretty_result = json.dumps(rows, ensure_ascii=False, indent=2)
                if truncated:
                    pretty_result += f"\n\n[Resultado limitado a {MAX_ROWS} filas]"
            else:
                pretty_result = "No se encontró ese dato en la base de datos."
            self.update_result_text(pretty_result)

            self.last_rows = rows
            self.last_query = query

            status = f"Consulta finalizada ({len(rows)} fila(s))" if rows else "Sin resultados"
            self.set_loading_state(False, status)
            logging.info("Consulta '%s' exitosa (%s filas)", query["name"], len(rows))

        except Exception:
            logging.exception("Error procesando resultado")
            self.handle_query_error("Se presentó un error al procesar el resultado.")

    def handle_query_error(self, message):
        self.set_loading_state(False, "Error")
        messagebox.showerror("Error", message)

    def generate_qr_from_result(self):
        if not self.last_rows:
            return
        payload = json.dumps(self.last_rows, ensure_ascii=False, separators=(",", ":"))
        try:
            image = generate_qr_image(payload)
        except (DataOverflowError, ValueError):
            messagebox.showerror(
                "QR demasiado grande", "El resultado es demasiado grande para generar un código QR."
            )
            return
        QrDisplayDialog(self.root, image)


def main():
    root = tk.Tk()
    DynamicQueryApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
