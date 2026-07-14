"""Chequeo rápido sin frameworks: python test_smoke.py"""
import dataclasses
import os
import tempfile

from app.exceptions import ConfigError, SQLSecurityError
from app.models import ConnectionConfig, QueryConfig
from app.repositories import ConnectionRepository
from app.security import validate_readonly_sql
from app.utils import query_allowed_on
from app.validators import validate_connection


def blocked(sql):
    try:
        validate_readonly_sql(sql)
        return False
    except SQLSecurityError:
        return True


# --- solo lectura ---
assert validate_readonly_sql("SELECT TOP 10 * FROM T WHERE X = ?")
assert validate_readonly_sql("select nombre from empleados where id = ?")
assert blocked("UPDATE T SET X = 1")
assert blocked("SELECT * FROM T; DROP TABLE T")
assert blocked("SELECT * INTO #tmp FROM T")          # tablas temporales
assert blocked("EXEC sp_who")                        # procedimientos
assert blocked("USE otra_base SELECT 1")
assert blocked("insert into T values (1)")           # minúsculas
assert not blocked("SELECT 'delete' AS palabra FROM T WHERE X = 1")  # literal no bloquea

# --- allowed_connections ---
assert query_allowed_on(QueryConfig(name="q"), "Prod")                                # sin campo = todas
assert query_allowed_on(QueryConfig(name="q", allowed_connections=[]), "Prod")         # vacío = todas
assert query_allowed_on(QueryConfig(name="q", allowed_connections=["Prod"]), "Prod")
assert not query_allowed_on(QueryConfig(name="q", allowed_connections=["Test"]), "Prod")

# --- validación de conexiones ---
tmp = os.path.join(tempfile.gettempdir(), "test_conns.json")
if os.path.exists(tmp):
    os.remove(tmp)
store = ConnectionRepository(tmp, validate_connection, ConnectionConfig.from_dict)

valid = ConnectionConfig(
    name="Test", driver="ODBC Driver 17 for SQL Server", server="srv",
    database="db", auth_type="windows", username="", password_ref="",
    timeout=5, active=True,
)
store.add(valid)
assert store.find_by_name("Test")

for bad_field, bad_value in [
    ("name", " "), ("server", ""), ("auth_type", "otro"), ("timeout", 0), ("timeout", "5"),
]:
    overrides = {"name": "Otra", bad_field: bad_value}  # bad_field puede repetir "name"; gana el último
    bad = dataclasses.replace(valid, **overrides)
    try:
        store.add(bad)
        raise AssertionError(f"debió rechazar {bad_field}={bad_value!r}")
    except ConfigError:
        pass

bad_sql_auth = dataclasses.replace(valid, name="SinUsuario", auth_type="sql_server", username="")
try:
    store.add(bad_sql_auth)
    raise AssertionError("debió exigir usuario con auth sql_server")
except ConfigError:
    pass

# --- round-trip JSON: guardar y volver a cargar debe reproducir los mismos datos ---
store2 = ConnectionRepository(tmp, validate_connection, ConnectionConfig.from_dict)
assert store2.find_by_name("Test") == valid

os.remove(tmp)
print("OK: todas las verificaciones pasaron")
