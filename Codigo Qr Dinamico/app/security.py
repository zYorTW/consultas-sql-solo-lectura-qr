"""Puerta de seguridad de solo lectura y acceso al Administrador de credenciales de Windows.

`validate_readonly_sql` es la validación no negociable: se ejecuta al guardar una consulta
(`validators.validate_query`) y otra vez justo antes de ejecutarla (`gui.execution_controller`).
Cualquier cambio aquí debe seguir pasando `test_security.py`.
"""
import logging
import re

import keyring

from app.config import KEYRING_SERVICE
from app.exceptions import SQLSecurityError

_BLOCKED_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "MERGE", "EXEC", "EXECUTE", "USE", "GRANT", "REVOKE", "DENY",
    "BACKUP", "RESTORE", "PUT", "INTO",
    # Administración / DoS / exfiltración (no pedidos explícitamente, pero
    # violan el principio de solo lectura tanto como los anteriores).
    "SHUTDOWN", "KILL", "DBCC", "RECONFIGURE", "WAITFOR", "BULK",
    "OPENROWSET", "OPENQUERY", "OPENDATASOURCE",
]
_BLOCKED_PREFIXES = ["XP_", "SP_"]

_COMMENT_RE = re.compile(r"--.*?$|/\*.*?\*/", re.MULTILINE | re.DOTALL)
_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_TOP_RE = re.compile(r"SELECT\s+TOP\s*\(?\s*\d+", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)


def strip_sql_noise(sql):
    """Quita comentarios y literales de texto para que la validación no los confunda con SQL real."""
    no_comments = _COMMENT_RE.sub(" ", sql)
    return _STRING_RE.sub("''", no_comments)


def validate_readonly_sql(sql):
    """Lanza SQLSecurityError si la consulta no es un SELECT único de solo lectura."""
    if not sql or not sql.strip():
        raise SQLSecurityError("La consulta SQL no puede estar vacía.")

    cleaned = strip_sql_noise(sql).strip()

    statements = [s for s in cleaned.split(";") if s.strip()]
    if len(statements) > 1:
        raise SQLSecurityError("No se permiten múltiples sentencias SQL separadas por ';'.")
    if not statements:
        raise SQLSecurityError("La consulta SQL no puede estar vacía.")

    stripped = statements[0].strip()
    if not re.match(r"^SELECT\b", stripped, re.IGNORECASE):
        raise SQLSecurityError("Solo se permiten consultas que inicien con SELECT.")

    upper = stripped.upper()
    for kw in _BLOCKED_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            raise SQLSecurityError(f"La palabra clave '{kw}' no está permitida en consultas de solo lectura.")
    for prefix in _BLOCKED_PREFIXES:
        if re.search(rf"\b{prefix}\w*", upper):
            raise SQLSecurityError(f"No se permite invocar procedimientos con prefijo '{prefix}'.")

    return True


def needs_row_filter_warning(sql):
    return not _TOP_RE.search(sql) and not _WHERE_RE.search(sql)


# ==========================================
# CONTRASEÑAS (Administrador de credenciales de Windows)
# ==========================================
def save_password(password_ref, password):
    keyring.set_password(KEYRING_SERVICE, password_ref, password)


def get_password(password_ref):
    if not password_ref:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, password_ref)
    except Exception:
        logging.exception("No se pudo leer la credencial '%s' del almacén de Windows", password_ref)
        return None


def delete_password(password_ref):
    if not password_ref:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, password_ref)
    except Exception:
        # La credencial puede no existir; no es un error fatal.
        logging.info("No se eliminó la credencial '%s' (posiblemente no existía)", password_ref)
