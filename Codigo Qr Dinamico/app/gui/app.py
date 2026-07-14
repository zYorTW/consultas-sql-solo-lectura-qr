"""Ventana principal: arma el layout compartido y conecta los tres controladores.

`DynamicQueryApp` ya no implementa CRUD de conexiones/consultas ni la ejecución de
consultas — eso vive en `ConnectionController`, `QueryController` y `ExecutionController`
respectivamente (ver esos módulos). Esta clase solo:
  1. construye los `LabelFrame`/`Frame` compartidos, en el mismo orden que la versión
     monolítica original (para que el layout visual no cambie), y
  2. es el único lugar que conoce a los tres controladores a la vez, así que es donde
     vive el estado "hay una operación en curso" que los deshabilita a todos.
"""
import tkinter as tk
from tkinter import ttk, messagebox

from app.config import CONNECTIONS_FILE, QUERIES_FILE
from app.gui.connection_controller import ConnectionController
from app.gui.execution_controller import ExecutionController
from app.gui.query_controller import QueryController
from app.repositories import ConnectionRepository, JsonListRepository
from app.validators import validate_connection, validate_query


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

        self.is_query_running = False

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        conn_frame = ttk.LabelFrame(main, text="Conexión / base de datos", padding=10)
        conn_frame.pack(fill="x", pady=(0, 10))

        selector_frame = ttk.LabelFrame(main, text="Consulta guardada", padding=10)
        selector_frame.pack(fill="x", pady=(0, 10))

        preview_frame = ttk.LabelFrame(main, text="Vista previa SQL", padding=8)
        preview_frame.pack(fill="x", pady=(0, 10))

        params_frame = ttk.LabelFrame(main, text="Parámetros", padding=8)
        params_frame.pack(fill="x", pady=(0, 10))

        action_frame = ttk.Frame(main)
        action_frame.pack(fill="x", pady=(0, 10))

        result_frame = ttk.LabelFrame(main, text="Resultado JSON", padding=10)
        result_frame.pack(fill="both", expand=True, pady=(0, 10))

        conn_repo = ConnectionRepository(CONNECTIONS_FILE, validate_connection)
        query_repo = JsonListRepository(QUERIES_FILE, validate_query)
        for repo in (conn_repo, query_repo):
            if repo.load_error:
                messagebox.showerror("Error de configuración", repo.load_error)

        self.connection_controller = ConnectionController(
            conn_frame, root, conn_repo,
            on_selection_changed=self._on_connection_changed,
            is_busy=lambda: self.is_query_running,
            set_loading_state=self.set_loading_state,
        )
        self.query_controller = QueryController(
            selector_frame, preview_frame, params_frame, root, query_repo,
            get_connection_names=self.connection_controller.all_connection_names,
            get_current_connection_name=self.connection_controller.selected_name,
        )
        self.execution_controller = ExecutionController(
            action_frame, result_frame, root,
            get_connection=self.connection_controller.get_selected_connection,
            get_query=self.query_controller.get_selected_query,
            get_param_values=self.query_controller.get_param_values,
            set_loading_state=self.set_loading_state,
            is_busy=lambda: self.is_query_running,
        )
        self.query_controller.on_query_changed = self.execution_controller.clear_result_and_qr

        # Todos los botones que se bloquean mientras hay una operación en curso.
        self.action_buttons = (
            self.connection_controller.buttons
            + self.query_controller.buttons
            + self.execution_controller.buttons
        )

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.connection_controller.refresh_connection_list(auto_select=False)

    def _on_connection_changed(self, conn_name, auto_select):
        self.query_controller.refresh_query_list(
            conn_name, select_name=self.query_controller.selected_name() or None, auto_select=auto_select
        )

    def set_loading_state(self, is_loading, message="Listo"):
        self.is_query_running = is_loading
        state = "disabled" if is_loading else "normal"
        for btn in self.action_buttons:
            btn.config(state=state)
        if not is_loading:
            self.execution_controller.refresh_qr_button_state()
        combo_state = "disabled" if is_loading else "readonly"
        self.query_controller.combo.config(state=combo_state)
        self.connection_controller.combo.config(state=combo_state)
        self.execution_controller.status_var.set(message)

    def on_close(self):
        self.root.destroy()


def run():
    root = tk.Tk()
    DynamicQueryApp(root)
    root.mainloop()
