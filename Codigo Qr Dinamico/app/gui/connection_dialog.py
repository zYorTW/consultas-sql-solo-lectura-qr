"""Diálogo modal: crear/editar una conexión (con prueba de conexión en segundo plano)."""
import logging
import threading
import tkinter as tk
import uuid
from tkinter import ttk, messagebox

from app.database import test_connection
from app.exceptions import ConfigError
from app.gui.config_dialog import ConfigDialog
from app.models import ConnectionConfig
from app.security import delete_password, get_password, save_password


class ConnectionEditorDialog(ConfigDialog):
    entity_label = "conexión"
    default_geometry = "600x440"

    def __init__(self, parent, store, existing=None, on_saved=None):
        self.is_testing = False
        super().__init__(parent, store, existing=existing, on_saved=on_saved)

    def _after_init(self):
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
        self.name_var.set(self.existing.name)
        self.driver_var.set(self.existing.driver)
        self.server_var.set(self.existing.server)
        self.database_var.set(self.existing.database)
        self.auth_var.set(self.existing.auth_type)
        self.user_var.set(self.existing.username)
        self.timeout_var.set(str(self.existing.timeout))
        self.active_var.set(self.existing.active)

    def _collect_config(self):
        try:
            timeout = int(self.timeout_var.get().strip() or "5")
        except ValueError:
            raise ConfigError("El timeout debe ser un número entero de segundos.")

        return ConnectionConfig(
            name=self.name_var.get().strip(),
            driver=self.driver_var.get().strip(),
            server=self.server_var.get().strip(),
            database=self.database_var.get().strip(),
            auth_type=self.auth_var.get(),
            username=self.user_var.get().strip(),
            password_ref=self.existing.password_ref if self.existing else "",
            timeout=timeout,
            active=self.active_var.get(),
        )

    def on_test(self):
        if self.is_testing:
            return
        try:
            cfg = self._collect_config()
            self.store.validate(cfg)
        except ConfigError as e:
            messagebox.showerror("Datos incompletos", str(e), parent=self)
            return

        typed = self.password_entry.get() or None
        if cfg.auth_type == "sql_server" and not typed and not get_password(cfg.password_ref):
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
            logging.exception("Prueba de conexión fallida ('%s')", cfg.name)
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

        if cfg.auth_type == "sql_server":
            ref = cfg.password_ref or uuid.uuid4().hex
            if not typed and not get_password(ref):
                messagebox.showerror(
                    "No se puede guardar",
                    "Debes ingresar la contraseña para esta conexión.",
                    parent=self,
                )
                return
            cfg.password_ref = ref
        else:
            # Al pasar a autenticación de Windows, limpiar la credencial almacenada.
            if cfg.password_ref:
                delete_password(cfg.password_ref)
            cfg.password_ref = ""

        try:
            if self.original_name:
                self.store.update(self.original_name, cfg)
            else:
                self.store.add(cfg)
        except ConfigError as e:
            messagebox.showerror("No se puede guardar", str(e), parent=self)
            return

        if cfg.auth_type == "sql_server" and typed:
            try:
                save_password(cfg.password_ref, typed)
            except Exception:
                logging.exception("No se pudo guardar la contraseña en el almacén de Windows")
                messagebox.showerror(
                    "Contraseña no guardada",
                    "La conexión se guardó, pero no se pudo guardar la contraseña en el "
                    "Administrador de credenciales de Windows. Edita la conexión e inténtalo de nuevo.",
                    parent=self,
                )

        if self.on_saved:
            self.on_saved(cfg.name)
        self.destroy()
