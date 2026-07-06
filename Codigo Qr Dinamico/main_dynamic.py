import json
import logging
import os
import re
import threading
import tkinter as tk
from datetime import date, datetime, time
from decimal import Decimal
from logging.handlers import RotatingFileHandler
from tkinter import ttk, messagebox

import pyodbc
import qrcode
from qrcode.exceptions import DataOverflowError
from PIL import Image, ImageTk
from dotenv import load_dotenv

# ==========================================
# CONFIGURACIÓN DE ENTORNO Y LOGGING
# ==========================================
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        RotatingFileHandler("app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    ],
)

DB_CONFIG = {
    "driver": os.getenv("DB_DRIVER"),
    "server": os.getenv("DB_SERVER"),
    "database": os.getenv("DB_DATABASE"),
    "username": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "use_windows_auth": os.getenv("DB_WINDOWS_AUTH", "False").strip().lower() == "true",
    "timeout": int(os.getenv("DB_TIMEOUT", 5)),
}

CONFIG_FILE = "queries_config.json"
PARAM_TYPES = ["str", "int", "float", "date"]
MAX_ROWS = 500  # ponytail: hard client-side cap; raise via .env if a real need for more shows up


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


# ==========================================
# ALMACÉN DE CONFIGURACIONES (queries_config.json)
# ==========================================
class QueryConfigStore:
    def __init__(self, path=CONFIG_FILE):
        self.path = path
        self.queries = []
        self.load_error = None
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            self.save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.queries = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.exception("Error leyendo %s", self.path)
            self.queries = []
            self.load_error = f"No se pudo leer '{self.path}': {e}"

    def save(self):
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.queries, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    def active_queries(self):
        return [q for q in self.queries if q.get("active", True)]

    def find_by_name(self, name):
        return next((q for q in self.queries if q["name"] == name), None)

    def add(self, query):
        self._validate(query)
        if self.find_by_name(query["name"]):
            raise ConfigError(f"Ya existe una consulta llamada '{query['name']}'.")
        self.queries.append(query)
        self.save()

    def update(self, original_name, query):
        self._validate(query)
        idx = next((i for i, q in enumerate(self.queries) if q["name"] == original_name), None)
        if idx is None:
            raise ConfigError("La consulta a editar ya no existe.")
        if query["name"] != original_name and self.find_by_name(query["name"]):
            raise ConfigError(f"Ya existe una consulta llamada '{query['name']}'.")
        self.queries[idx] = query
        self.save()

    def delete(self, name):
        self.queries = [q for q in self.queries if q["name"] != name]
        self.save()

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
def build_connection_string():
    driver = DB_CONFIG["driver"]
    server = DB_CONFIG["server"]
    database = DB_CONFIG["database"]

    if not driver:
        raise ValueError("Falta configurar DB_DRIVER en el archivo .env.")
    if not server:
        raise ValueError("Falta configurar DB_SERVER en el archivo .env.")
    if not database:
        raise ValueError("Falta configurar DB_DATABASE en el archivo .env.")

    if DB_CONFIG["use_windows_auth"]:
        return (
            f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
            f"Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes;"
        )

    username = DB_CONFIG["username"]
    password = DB_CONFIG["password"]
    if not username or not password:
        raise ValueError(
            "Debes configurar DB_USER y DB_PASSWORD en el .env cuando DB_WINDOWS_AUTH=False."
        )

    return (
        f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
        f"UID={username};PWD={password};Encrypt=yes;TrustServerCertificate=yes;"
    )


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


def fetch_query_data(conn_str, sql, params):
    conn = pyodbc.connect(conn_str, timeout=DB_CONFIG["timeout"])
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
# DIÁLOGO: CREAR / EDITAR CONSULTA
# ==========================================
class QueryEditorDialog(tk.Toplevel):
    def __init__(self, parent, store, existing=None, on_saved=None):
        super().__init__(parent)
        self.store = store
        self.existing = existing
        self.on_saved = on_saved
        self.original_name = existing["name"] if existing else None
        self.param_rows = []

        self.title("Editar consulta" if existing else "Nueva consulta")
        self.geometry("720x640")
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
        for p in self.existing.get("params", []):
            self.add_param_row(p)

    def on_save(self):
        query = {
            "name": self.name_var.get().strip(),
            "description": self.desc_var.get().strip(),
            "sql": self.sql_text.get("1.0", "end").strip(),
            "generate_qr": self.generate_qr_var.get(),
            "active": self.active_var.get(),
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
            messagebox.showerror("No se puede guardar", str(e))
            return

        if self.on_saved:
            self.on_saved()
        self.destroy()


# ==========================================
# APLICACIÓN PRINCIPAL
# ==========================================
class DynamicQueryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sucroal - Consultas Dinámicas SQL (solo lectura)")
        self.root.geometry("1000x820")
        self.root.minsize(800, 620)

        self.qr_photo = None
        self.qr_image_pil = None
        self.is_query_running = False
        self.param_widgets = []

        self.store = QueryConfigStore()
        if self.store.load_error:
            messagebox.showerror("Error de configuración", self.store.load_error)

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Configure>", self._on_window_resize)
        self.refresh_query_list()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        selector_frame = ttk.LabelFrame(main, text="Consulta guardada", padding=10)
        selector_frame.pack(fill="x", pady=(0, 10))

        self.query_var = tk.StringVar()
        self.query_combo = ttk.Combobox(
            selector_frame, textvariable=self.query_var, state="readonly", width=45
        )
        self.query_combo.grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.query_combo.bind("<<ComboboxSelected>>", lambda e: self.on_query_selected())

        ttk.Button(selector_frame, text="Nueva consulta", command=self.new_query).grid(row=0, column=1, padx=5)
        ttk.Button(selector_frame, text="Editar consulta", command=self.edit_query).grid(row=0, column=2, padx=5)
        ttk.Button(selector_frame, text="Eliminar consulta", command=self.delete_query).grid(row=0, column=3, padx=5)

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

        self.status_var = tk.StringVar(value="Listo")
        ttk.Label(action_frame, textvariable=self.status_var).pack(side="left", padx=15)

        result_frame = ttk.LabelFrame(main, text="Resultado JSON", padding=10)
        result_frame.pack(fill="both", expand=True, pady=(0, 10))
        text_container = ttk.Frame(result_frame)
        text_container.pack(fill="both", expand=True)
        self.result_text = tk.Text(text_container, height=12, wrap="word", state="disabled")
        result_scroll = ttk.Scrollbar(text_container, orient="vertical", command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=result_scroll.set)
        self.result_text.pack(side="left", fill="both", expand=True)
        result_scroll.pack(side="right", fill="y")

        qr_frame = ttk.LabelFrame(main, text="Código QR", padding=10)
        qr_frame.pack(fill="both", expand=True)
        self.qr_label = ttk.Label(qr_frame, text="Aquí aparecerá el QR", anchor="center")
        self.qr_label.pack(fill="both", expand=True)

    # --- gestión de la lista de consultas ---
    def refresh_query_list(self, select_name=None):
        names = [q["name"] for q in self.store.active_queries()]
        self.query_combo["values"] = names
        if select_name and select_name in names:
            self.query_var.set(select_name)
        elif names:
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

    # --- CRUD ---
    def new_query(self):
        QueryEditorDialog(self.root, self.store, on_saved=self.refresh_query_list)

    def edit_query(self):
        query = self.get_selected_query()
        if not query:
            messagebox.showinfo("Editar consulta", "Selecciona una consulta primero.")
            return
        QueryEditorDialog(
            self.root, self.store, existing=query,
            on_saved=lambda: self.refresh_query_list(select_name=query["name"])
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

    def _on_window_resize(self, event):
        if self.qr_image_pil is not None:
            self.refresh_qr_display()

    def set_loading_state(self, is_loading, message="Listo"):
        self.is_query_running = is_loading
        state = "disabled" if is_loading else "normal"
        self.run_button.config(state=state)
        self.clear_button.config(state=state)
        self.query_combo.config(state="disabled" if is_loading else "readonly")
        self.status_var.set(message)

    def clear_result_and_qr(self):
        self.update_result_text("")
        self.qr_label.config(image="", text="Aquí aparecerá el QR")
        self.qr_photo = None
        self.qr_image_pil = None

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

        query = self.get_selected_query()
        if not query:
            messagebox.showinfo("Ejecutar consulta", "Selecciona una consulta primero.")
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
            target=self._run_query_thread, args=(query, params_values), daemon=True
        )
        thread.start()

    def _run_query_thread(self, query, params_values):
        try:
            logging.info("Ejecutando consulta '%s'", query["name"])
            conn_str = build_connection_string()
            rows, truncated = fetch_query_data(conn_str, query["sql"], params_values)
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
            pretty_result = json.dumps(rows, ensure_ascii=False, indent=2)
            if truncated:
                pretty_result += f"\n\n[Resultado limitado a {MAX_ROWS} filas]"
            self.update_result_text(pretty_result)

            if query.get("generate_qr") and rows:
                payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
                try:
                    self.qr_image_pil = generate_qr_image(payload)
                    self.refresh_qr_display()
                except DataOverflowError:
                    self.qr_label.config(image="", text="El resultado es demasiado grande para un QR.")
                    self.qr_photo = None
                    self.qr_image_pil = None
            else:
                self.qr_label.config(image="", text="Aquí aparecerá el QR")

            self.set_loading_state(False, f"Consulta finalizada ({len(rows)} fila(s))")
            logging.info("Consulta '%s' exitosa (%s filas)", query["name"], len(rows))

        except Exception:
            logging.exception("Error procesando resultado")
            self.handle_query_error("Se presentó un error al procesar el resultado.")

    def handle_query_error(self, message):
        self.set_loading_state(False, "Error")
        messagebox.showerror("Error", message)

    def get_qr_display_size(self):
        self.root.update_idletasks()
        label_width = self.qr_label.winfo_width()
        label_height = self.qr_label.winfo_height()
        if label_width < 100:
            label_width = 350
        if label_height < 100:
            label_height = 350
        size = min(label_width - 20, label_height - 20)
        if size < 150:
            size = 150
        if size > 500:
            size = 500
        return size

    def refresh_qr_display(self):
        if self.qr_image_pil is None:
            return
        size = self.get_qr_display_size()
        img_resized = self.qr_image_pil.copy().resize((size, size), Image.Resampling.NEAREST)
        self.qr_photo = ImageTk.PhotoImage(img_resized)
        self.qr_label.config(image=self.qr_photo, text="")


def main():
    root = tk.Tk()
    DynamicQueryApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
