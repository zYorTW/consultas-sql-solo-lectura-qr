"""Excepciones compartidas entre security/validators/repositories (evita imports circulares)."""


class SQLSecurityError(Exception):
    """La sentencia SQL no pasó el filtro de solo lectura."""


class ConfigError(Exception):
    """Una conexión o consulta guardada no cumple sus reglas de validación."""
