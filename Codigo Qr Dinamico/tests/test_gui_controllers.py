"""Controladores GUI con un Tk oculto (sin BD/keyring reales): python test_gui_controllers.py

Usa una ventana Tk real (root.withdraw(), nunca se muestra) porque los controladores son
clases de Tkinter de verdad — mockearlo perdería justo lo que hay que probar (que los
widgets reaccionan bien). Lo que sí se mockea es todo lo externo: pyodbc (vía
execution_controller.fetch_query_data) y los messagebox/threading que colgarían la prueba
esperando un clic o un hilo real.
"""
import os
import tempfile
import tkinter as tk
from unittest.mock import MagicMock, patch

import pyodbc

from app.gui import connection_controller as cc_module
from app.gui import execution_controller as ec_module
from app.gui import query_controller as qc_module
from app.gui.connection_controller import ConnectionController
from app.gui.execution_controller import ExecutionController
from app.gui.query_controller import QueryController
from app.models import ConnectionConfig, ParamConfig, QueryConfig
from app.repositories import ConnectionRepository, JsonListRepository
from app.validators import validate_connection, validate_query


def make_repo(cls, path, validate, from_dict, items):
    if os.path.exists(path):
        os.remove(path)
    repo = cls(path, validate, from_dict)
    repo.items = items
    repo.save()
    repo.load()
    return repo


class ImmediateThread:
    """Reemplaza threading.Thread dentro de execution_controller: corre el target ya
    mismo, en el mismo hilo, para no depender de sincronización real en la prueba."""

    def __init__(self, target=None, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


class FakeThreadingModule:
    Thread = ImmediateThread


root = tk.Tk()
root.withdraw()

# Ningún messagebox real debe abrirse durante la prueba: si algo dispara uno sin que lo
# hayamos anticipado, mejor que la prueba lo ignore a que se cuelgue esperando un clic.
for mod in (cc_module, qc_module, ec_module):
    mod.messagebox.showinfo = lambda *a, **k: None
    mod.messagebox.showerror = lambda *a, **k: None
    mod.messagebox.askyesno = lambda *a, **k: True


# ============================================================
# ConnectionController
# ============================================================
conn_path = os.path.join(tempfile.gettempdir(), "test_gui_conns.json")
conns = [
    ConnectionConfig(name="Prod", driver="d", server="s1", database="db1", auth_type="windows", active=True),
    ConnectionConfig(name="Vieja", driver="d", server="s2", database="db2", auth_type="sql_server",
                      username="u", active=False),
]
conn_repo = make_repo(ConnectionRepository, conn_path, validate_connection, ConnectionConfig.from_dict, conns)

changes = []
conn_frame = tk.Frame(root)
conn_ctrl = ConnectionController(
    conn_frame, root, conn_repo,
    on_selection_changed=lambda name, auto: changes.append((name, auto)),
    is_busy=lambda: False,
    set_loading_state=lambda loading, msg="Listo": None,
)
conn_ctrl.refresh_connection_list(auto_select=True)
assert list(conn_ctrl.combo["values"]) == ["Prod"]  # "Vieja" está inactiva, no aparece
assert conn_ctrl.selected_name() == "Prod"
assert "Autenticación: Windows" in conn_ctrl.conn_info_var.get()
assert changes and changes[-1][0] == "Prod"
assert conn_ctrl.all_connection_names() == ["Prod", "Vieja"]  # incluye inactivas

conn_ctrl.delete_connection()  # elimina "Prod" (la seleccionada) tras confirmar (mockeado a True)
assert conn_repo.find_by_name("Prod") is None

os.remove(conn_path)


# ============================================================
# QueryController
# ============================================================
query_path = os.path.join(tempfile.gettempdir(), "test_gui_queries.json")
queries = [
    QueryConfig(name="Q1", sql="SELECT 1", allowed_connections=[]),
    QueryConfig(name="Q2", sql="SELECT ?", allowed_connections=["Otra"],
                params=[ParamConfig(name="p1", label="P1", type="int")]),
]
query_repo = make_repo(JsonListRepository, query_path, validate_query, QueryConfig.from_dict, queries)

selector_frame = tk.Frame(root)
preview_frame = tk.Frame(root)
params_frame = tk.Frame(root)
query_ctrl = QueryController(
    selector_frame, preview_frame, params_frame, root, query_repo,
    get_connection_names=lambda: ["Prod", "Otra"],
    get_current_connection_name=lambda: "Prod",
)

query_ctrl.refresh_query_list("Prod", auto_select=True)
assert list(query_ctrl.combo["values"]) == ["Q1"]  # Q2 solo corre en "Otra"

query_ctrl.refresh_query_list("Otra", auto_select=True)
assert set(query_ctrl.combo["values"]) == {"Q1", "Q2"}

query_ctrl.query_var.set("Q2")
query_ctrl.on_query_selected()
assert len(query_ctrl.param_widgets) == 1
_, var = query_ctrl.param_widgets[0]

var.set("42")
assert query_ctrl.get_param_values() == [42]

var.set("")
try:
    query_ctrl.get_param_values()
    raise AssertionError("debió exigir el parámetro requerido")
except ValueError:
    pass

os.remove(query_path)


# ============================================================
# ExecutionController
# ============================================================
action_frame = tk.Frame(root)
result_frame = tk.Frame(root)

state = {"connection": None, "query": None, "param_values": []}
loading_calls = []
exec_ctrl = ExecutionController(
    action_frame, result_frame, root,
    get_connection=lambda: state["connection"],
    get_query=lambda: state["query"],
    get_param_values=lambda: state["param_values"],
    set_loading_state=lambda loading, msg="Listo": loading_calls.append((loading, msg)),
    is_busy=lambda: False,
)

# --- guardas síncronas de run_query (nada de hilos reales) ---
loading_calls.clear()
exec_ctrl.run_query()  # sin conexión seleccionada
assert not loading_calls  # nunca llegó a "Consultando..."

state["connection"] = ConnectionConfig(name="Prod", driver="d", server="s", database="db", auth_type="windows")
exec_ctrl.run_query()  # sin consulta seleccionada
assert not loading_calls

state["query"] = QueryConfig(name="Q1", sql="SELECT 1", allowed_connections=["Otra"])
exec_ctrl.run_query()  # no permitida en "Prod"
assert not loading_calls

state["query"] = QueryConfig(name="Q1", sql="DROP TABLE T")
exec_ctrl.run_query()  # bloqueada por seguridad
assert not loading_calls

state["query"] = QueryConfig(name="Q1", sql="SELECT 1")


def raise_value_error():
    raise ValueError("parámetro inválido")


state_param_values = state["param_values"]
exec_ctrl.get_param_values = raise_value_error
exec_ctrl.run_query()  # parámetro inválido
assert not loading_calls
exec_ctrl.get_param_values = lambda: state["param_values"]

# --- camino feliz: ejecuta de verdad la tubería con un hilo síncrono y fetch_query_data mockeado ---
with patch.object(ec_module, "threading", FakeThreadingModule), \
     patch.object(ec_module, "fetch_query_data", return_value=([{"id": 1}], False)):
    loading_calls.clear()
    exec_ctrl.run_query()
    root.update()  # procesa el self.root.after(0, ...) que agenda handle_query_success

assert loading_calls[0] == (True, "Consultando...")
assert exec_ctrl.last_rows == [{"id": 1}]
assert exec_ctrl.last_query.name == "Q1"
assert '"id": 1' in exec_ctrl.result_text.get("1.0", "end")
exec_ctrl.refresh_qr_button_state()
assert str(exec_ctrl.qr_button["state"]) == "normal"

# --- camino de error: fetch_query_data lanza una excepción de pyodbc ---
with patch.object(ec_module, "threading", FakeThreadingModule), \
     patch.object(ec_module, "fetch_query_data", side_effect=pyodbc.InterfaceError("x", "no conecta")):
    exec_ctrl.run_query()
    root.update()

assert loading_calls[-1] == (False, "Error")

# --- clear_result_and_qr / clear_all ---
exec_ctrl.clear_result_and_qr()
assert exec_ctrl.last_rows is None and exec_ctrl.last_query is None
assert str(exec_ctrl.qr_button["state"]) == "disabled"

# --- generate_qr_from_result: payload normal abre el diálogo; uno gigante muestra error ---
with patch.object(ec_module, "QrDisplayDialog") as mock_dialog:
    exec_ctrl.last_rows = [{"id": 1}]
    exec_ctrl.generate_qr_from_result()
    mock_dialog.assert_called_once()

with patch.object(ec_module, "QrDisplayDialog") as mock_dialog:
    exec_ctrl.last_rows = [{"campo": "x" * 5000}]  # excede la capacidad de un QR
    exec_ctrl.generate_qr_from_result()
    mock_dialog.assert_not_called()

root.destroy()
print("OK: controladores GUI verificados con Tk oculto y pyodbc mockeado")
