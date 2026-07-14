"""Acceso a datos vía pyodbc: connection string, prueba de conexión, ejecución parametrizada."""
from datetime import date, datetime, time
from decimal import Decimal

import pyodbc

from app.config import MAX_ROWS
from app.security import get_password


def build_connection_string(conn_cfg, password=None):
    """Arma el connection string de pyodbc para una conexión guardada.
    'password' permite probar con una contraseña recién digitada sin guardarla."""
    name = conn_cfg.get("name", "?")
    driver = conn_cfg.get("driver", "").strip()
    server = conn_cfg.get("server", "").strip()
    database = conn_cfg.get("database", "").strip()

    if not driver or not server or not database:
        raise ValueError(f"La conexión '{name}' está incompleta (driver, servidor o base de datos).")

    base = (
        f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
        f"Encrypt=yes;TrustServerCertificate=yes;"
    )

    if conn_cfg.get("auth_type") == "windows":
        return base + "Trusted_Connection=yes;"

    username = conn_cfg.get("username", "").strip()
    pwd = password or get_password(conn_cfg.get("password_ref", ""))

    if not username:
        raise ValueError(f"La conexión '{name}' no tiene usuario configurado.")
    if not pwd:
        raise ValueError(
            f"La conexión '{name}' no tiene contraseña guardada en el almacén de credenciales. "
            "Edita la conexión y vuelve a ingresar la contraseña."
        )

    return base + f"UID={username};PWD={pwd};"


def test_connection(conn_cfg, password=None):
    """Abre la conexión y ejecuta SELECT 1. Lanza excepción si algo falla."""
    conn = pyodbc.connect(
        build_connection_string(conn_cfg, password),
        timeout=conn_cfg.get("timeout", 5),
    )
    try:
        conn.cursor().execute("SELECT 1").fetchone()
    finally:
        conn.close()


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


def fetch_query_data(conn_cfg, sql, params):
    conn = pyodbc.connect(
        build_connection_string(conn_cfg),
        timeout=conn_cfg.get("timeout", 5),
    )
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
