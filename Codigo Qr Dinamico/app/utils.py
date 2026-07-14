"""Utilidades pequeñas sin un módulo propio más específico."""


def query_allowed_on(query, connection_name):
    """True si la consulta puede ejecutarse en la conexión dada.
    Lista 'allowed_connections' vacía o ausente = permitida en todas."""
    allowed = query.get("allowed_connections") or []
    return not allowed or connection_name in allowed
