"""Sección 'Conexión / base de datos': combo + CRUD + prueba de conexión.

`is_busy`/`set_loading_state` son inyectados por `DynamicQueryApp` porque "hay una operación
en curso" es un estado compartido con QueryController/ExecutionController (deshabilita los
botones de los tres), no algo que esta clase pueda decidir por sí sola.
"""
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from app.database import test_connection
from app.gui.connection_dialog import ConnectionEditorDialog


class ConnectionController:
    def __init__(self, conn_frame, root, repo, on_selection_changed, is_busy, set_loading_state):
        self.root = root
        self.repo = repo
        self.on_selection_changed = on_selection_changed
        self.is_busy = is_busy
        self.set_loading_state = set_loading_state

        self.conn_var = tk.StringVar()
        self.conn_combo = ttk.Combobox(conn_frame, textvariable=self.conn_var, state="readonly", width=40)
        self.conn_combo.grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.conn_combo.bind("<<ComboboxSelected>>", lambda e: self.on_connection_selected(auto_select=False))

        new_btn = ttk.Button(conn_frame, text="Nueva conexión", command=self.new_connection)
        new_btn.grid(row=0, column=1, padx=5)
        edit_btn = ttk.Button(conn_frame, text="Editar conexión", command=self.edit_connection)
        edit_btn.grid(row=0, column=2, padx=5)
        delete_btn = ttk.Button(conn_frame, text="Eliminar conexión", command=self.delete_connection)
        delete_btn.grid(row=0, column=3, padx=5)
        test_btn = ttk.Button(conn_frame, text="Probar conexión", command=self.test_selected_connection)
        test_btn.grid(row=0, column=4, padx=5)
        self.buttons = [new_btn, edit_btn, delete_btn, test_btn]

        self.conn_info_var = tk.StringVar()
        ttk.Label(conn_frame, textvariable=self.conn_info_var, foreground="#555").grid(
            row=1, column=0, columnspan=5, sticky="w", padx=5
        )

    @property
    def combo(self):
        return self.conn_combo

    def selected_name(self):
        return self.conn_var.get()

    def all_connection_names(self):
        return [c["name"] for c in self.repo.items]

    def refresh_connection_list(self, select_name=None, auto_select=True):
        names = [c["name"] for c in self.repo.active_items()]
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
        return self.repo.find_by_name(name) if name else None

    def on_connection_selected(self, auto_select=True):
        conn = self.get_selected_connection()
        if conn:
            auth = "Windows" if conn.get("auth_type") == "windows" else f"SQL ({conn.get('username', '')})"
            self.conn_info_var.set(
                f"{conn.get('server', '')}  /  {conn.get('database', '')}  —  Autenticación: {auth}"
            )
        elif not self.repo.active_items():
            self.conn_info_var.set("No hay conexiones. Crea una con 'Nueva conexión'.")
        else:
            self.conn_info_var.set("")
        self.on_selection_changed(self.conn_var.get() or None, auto_select)

    def new_connection(self):
        ConnectionEditorDialog(
            self.root, self.repo,
            on_saved=lambda name: self.refresh_connection_list(select_name=name),
        )

    def edit_connection(self):
        conn = self.get_selected_connection()
        if not conn:
            messagebox.showinfo("Editar conexión", "Selecciona una conexión primero.")
            return
        ConnectionEditorDialog(
            self.root, self.repo, existing=conn,
            on_saved=lambda name: self.refresh_connection_list(select_name=name, auto_select=False),
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
            self.repo.delete(conn["name"])
            self.refresh_connection_list()

    def test_selected_connection(self):
        if self.is_busy():
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
