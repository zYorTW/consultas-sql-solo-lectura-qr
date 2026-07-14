"""Acceso a datos con pyodbc mockeado, sin BD real: python test_database.py"""
from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import app.database as database
from app.models import ConnectionConfig


def windows_conn():
    return ConnectionConfig(name="W", driver="ODBC Driver 17 for SQL Server", server="srv", database="db",
                             auth_type="windows")


def sql_conn():
    return ConnectionConfig(name="S", driver="ODBC Driver 17 for SQL Server", server="srv", database="db",
                             auth_type="sql_server", username="user", password_ref="ref1")


def fake_cursor(rows, columns):
    cur = MagicMock()
    cur.description = [(c,) for c in columns]
    cur.fetchmany.return_value = rows
    return cur


# --- build_connection_string ---
cs = database.build_connection_string(windows_conn())
assert "Trusted_Connection=yes;" in cs and "UID=" not in cs

cs = database.build_connection_string(sql_conn(), password="secret")
assert "UID=user;PWD=secret;" in cs

with patch.object(database, "get_password", return_value=None):
    try:
        database.build_connection_string(sql_conn())
        raise AssertionError("debió fallar sin contraseña guardada ni provista")
    except ValueError:
        pass

try:
    database.build_connection_string(ConnectionConfig(name="X", auth_type="windows"))  # sin driver/server/db
    raise AssertionError("debió fallar por conexión incompleta")
except ValueError:
    pass

# --- normalize_value ---
assert database.normalize_value(None) is None
assert database.normalize_value(Decimal("3")) == 3 and isinstance(database.normalize_value(Decimal("3")), int)
assert database.normalize_value(Decimal("3.5")) == 3.5 and isinstance(database.normalize_value(Decimal("3.5")), float)
assert database.normalize_value(date(2024, 1, 2)) == "2024-01-02"
assert database.normalize_value(datetime(2024, 1, 2, 3, 4, 5)) == "2024-01-02T03:04:05"
assert database.normalize_value(time(3, 4, 5)) == "03:04:05"
assert database.normalize_value(b"hola") == "hola"
assert database.normalize_value(b"\xff\xfe") == str(b"\xff\xfe")  # bytes no-UTF8: repr como string

# --- fetch_query_data: truncamiento a MAX_ROWS y forma de las filas ---
with patch.object(database, "pyodbc") as mock_pyodbc, patch.object(database, "MAX_ROWS", 2):
    mock_conn = MagicMock()
    mock_pyodbc.connect.return_value = mock_conn
    mock_conn.cursor.return_value = fake_cursor([(1, "a"), (2, "b"), (3, "c")], ["id", "val"])
    rows, truncated = database.fetch_query_data(windows_conn(), "SELECT ...", [])
    assert truncated is True
    assert rows == [{"id": 1, "val": "a"}, {"id": 2, "val": "b"}]
    mock_conn.close.assert_called_once()

with patch.object(database, "pyodbc") as mock_pyodbc, patch.object(database, "MAX_ROWS", 5):
    mock_conn = MagicMock()
    mock_pyodbc.connect.return_value = mock_conn
    mock_conn.cursor.return_value = fake_cursor([(1, "a")], ["id", "val"])
    rows, truncated = database.fetch_query_data(windows_conn(), "SELECT ...", [])
    assert truncated is False
    assert rows == [{"id": 1, "val": "a"}]

# --- test_connection: cierra la conexión incluso si execute() falla ---
with patch.object(database, "pyodbc") as mock_pyodbc:
    mock_conn = MagicMock()
    mock_pyodbc.connect.return_value = mock_conn
    mock_conn.cursor.return_value.execute.side_effect = RuntimeError("boom")
    try:
        database.test_connection(windows_conn())
        raise AssertionError("debió propagar el error de execute()")
    except RuntimeError:
        pass
    mock_conn.close.assert_called_once()

print("OK: acceso a datos verificado con pyodbc mockeado")
