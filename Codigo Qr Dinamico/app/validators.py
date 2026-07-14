"""Reglas de validación puras (sin I/O) para conexiones y consultas guardadas.

Separado de `repositories.py`: un repositorio sabe cargar/guardar, un validador sabe si un
dict es válido. `repositories.JsonListRepository` invoca estas funciones antes de escribir.
"""
from datetime import datetime

from app.config import AUTH_TYPES, PARAM_TYPES
from app.exceptions import ConfigError
from app.security import strip_sql_noise, validate_readonly_sql


def validate_connection(conn):
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


def validate_query(query):
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
