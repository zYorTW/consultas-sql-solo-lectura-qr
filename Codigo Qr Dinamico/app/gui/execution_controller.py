"""Sección de ejecución: botones Ejecutar/Limpiar/Generar QR, estado, resultado JSON y QR.

`get_connection`/`get_query`/`get_param_values` son inyectados desde ConnectionController y
QueryController: esta clase no necesita saber cómo se seleccionan, solo qué está seleccionado
ahora mismo.
"""
import json
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import pyodbc
from qrcode.exceptions import DataOverflowError

from app.config import MAX_ROWS
from app.database import fetch_query_data
from app.exceptions import SQLSecurityError
from app.gui.qr_dialog import QrDisplayDialog
from app.qr import generate_qr_image
from app.security import needs_row_filter_warning, validate_readonly_sql
from app.utils import query_allowed_on


class ExecutionController:
    def __init__(self, action_frame, result_frame, root,
                 get_connection, get_query, get_param_values, set_loading_state, is_busy):
        self.root = root
        self.get_connection = get_connection
        self.get_query = get_query
        self.get_param_values = get_param_values
        self.set_loading_state = set_loading_state
        self.is_busy = is_busy

        self.last_rows = None
        self.last_query = None

        self.run_button = ttk.Button(action_frame, text="Ejecutar consulta", command=self.run_query)
        self.run_button.pack(side="left", padx=5)
        self.clear_button = ttk.Button(action_frame, text="Limpiar", command=self.clear_all)
        self.clear_button.pack(side="left", padx=5)
        self.qr_button = ttk.Button(action_frame, text="Generar QR", command=self.generate_qr_from_result)
        self.qr_button.pack(side="left", padx=5)
        self.qr_button.config(state="disabled")
        self.buttons = [self.run_button, self.clear_button, self.qr_button]

        self.status_var = tk.StringVar(value="Listo")
        ttk.Label(action_frame, textvariable=self.status_var).pack(side="left", padx=15)

        text_container = ttk.Frame(result_frame)
        text_container.pack(fill="both", expand=True)
        self.result_text = tk.Text(text_container, height=6, wrap="word", state="disabled")
        result_scroll = ttk.Scrollbar(text_container, orient="vertical", command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=result_scroll.set)
        self.result_text.pack(side="left", fill="both", expand=True)
        result_scroll.pack(side="right", fill="y")

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
        if self.is_busy():
            return
        self.clear_result_and_qr()
        self.status_var.set("Listo")

    def run_query(self):
        if self.is_busy():
            return

        conn_cfg = self.get_connection()
        if not conn_cfg:
            messagebox.showinfo("Ejecutar consulta", "Selecciona una conexión primero.")
            return

        query = self.get_query()
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
            params_values = self.get_param_values()
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
