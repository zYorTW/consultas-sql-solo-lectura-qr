"""Reglas de validación puras (sin I/O) para conexiones y consultas guardadas.

Separado de `repositories.py`: un repositorio sabe cargar/guardar, un validador sabe si una
instancia de `models.py` es válida. `repositories.JsonListRepository` invoca estas funciones
antes de escribir.
"""
from datetime import datetime

from app.config import AUTH_TYPES, PARAM_TYPES
from app.exceptions import ConfigError
from app.security import strip_sql_noise, validate_readonly_sql


def validate_connection(conn):
    if not conn.name.strip():
        raise ConfigError("El nombre de la conexión es obligatorio.")
    if not conn.driver.strip():
        raise ConfigError("Debes indicar el driver ODBC.")
    if not conn.server.strip():
        raise ConfigError("Debes indicar el servidor.")
    if not conn.database.strip():
        raise ConfigError("Debes indicar la base de datos.")
    if conn.auth_type not in AUTH_TYPES:
        raise ConfigError("El tipo de autenticación debe ser 'windows' o 'sql_server'.")
    if conn.auth_type == "sql_server" and not conn.username.strip():
        raise ConfigError("Debes indicar el usuario para autenticación SQL Server.")
    if not isinstance(conn.timeout, int) or conn.timeout < 1:
        raise ConfigError("El timeout debe ser un número entero de segundos (mínimo 1).")


def validate_query(query):
    if not query.name.strip():
        raise ConfigError("El nombre de la consulta es obligatorio.")
    if not query.sql.strip():
        raise ConfigError("La sentencia SQL es obligatoria.")

    validate_readonly_sql(query.sql)

    for p in query.params:
        if not p.name.strip():
            raise ConfigError("Cada parámetro debe tener un nombre.")
        if p.type not in PARAM_TYPES:
            raise ConfigError(f"Tipo de parámetro inválido: {p.type}")

    placeholder_count = strip_sql_noise(query.sql).count("?")
    if placeholder_count != len(query.params):
        raise ConfigError(
            f"La consulta tiene {placeholder_count} parámetro(s) '?' pero se definieron "
            f"{len(query.params)}. Deben coincidir en cantidad y orden."
        )

    if not isinstance(query.allowed_connections, list) or any(
        not isinstance(n, str) for n in query.allowed_connections
    ):
        raise ConfigError("'allowed_connections' debe ser una lista de nombres de conexión.")


def convert_param_value(raw_value, param):
    raw_value = (raw_value or "").strip()
    label = param.label or param.name

    if not raw_value:
        if param.required:
            raise ValueError(f"El parámetro '{label}' es obligatorio.")
        return None

    try:
        if param.type == "int":
            return int(raw_value)
        if param.type == "float":
            return float(raw_value)
        if param.type == "date":
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        return raw_value
    except ValueError:
        hint = " (formato AAAA-MM-DD)" if param.type == "date" else ""
        raise ValueError(f"El parámetro '{label}' debe ser de tipo {param.type}{hint}.")
