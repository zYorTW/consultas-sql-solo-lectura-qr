"""Formas de los dicts que viajan a/desde el JSON de configuración.

Son TypedDict a propósito (no dataclasses): la representación en tiempo de ejecución sigue
siendo un dict plano, igual que antes de este refactor, por lo que la persistencia JSON y
cada `.get(...)` existente siguen funcionando sin ningún cambio. Ver README de la
refactorización para la justificación completa.
"""
from typing import List, TypedDict


class ParamConfig(TypedDict, total=False):
    name: str
    label: str
    type: str  # uno de config.PARAM_TYPES
    required: bool


class QueryConfig(TypedDict, total=False):
    name: str
    description: str
    sql: str
    generate_qr: bool
    active: bool
    allowed_connections: List[str]
    params: List[ParamConfig]


class ConnectionConfig(TypedDict, total=False):
    name: str
    driver: str
    server: str
    database: str
    auth_type: str  # uno de config.AUTH_TYPES
    username: str
    password_ref: str
    timeout: int
    active: bool
