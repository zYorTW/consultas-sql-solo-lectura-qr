"""Dataclasses para lo que viaja a/desde el JSON de configuración.

`from_dict`/`to_dict` son explícitos (no `dataclasses.asdict`) para que el formato en disco
quede fijo y documentado aquí mismo, en vez de depender de cómo estén declarados los campos.
Los repositorios (`repositories.py`) llaman `from_dict` al cargar y `to_dict` al guardar; el
resto del código (validators, database, gui) trabaja con estas instancias por atributo.
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class ParamConfig:
    name: str
    label: str = ""
    type: str = "str"  # uno de config.PARAM_TYPES
    required: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "ParamConfig":
        return cls(
            name=d.get("name", ""),
            label=d.get("label") or d.get("name", ""),
            type=d.get("type", "str"),
            required=d.get("required", True),
        )

    def to_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "type": self.type, "required": self.required}


@dataclass
class QueryConfig:
    name: str
    description: str = ""
    sql: str = ""
    generate_qr: bool = True
    active: bool = True
    allowed_connections: List[str] = field(default_factory=list)
    params: List[ParamConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "QueryConfig":
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            sql=d.get("sql", ""),
            generate_qr=d.get("generate_qr", True),
            active=d.get("active", True),
            allowed_connections=list(d.get("allowed_connections") or []),
            params=[ParamConfig.from_dict(p) for p in d.get("params", [])],
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "sql": self.sql,
            "generate_qr": self.generate_qr,
            "active": self.active,
            "allowed_connections": self.allowed_connections,
            "params": [p.to_dict() for p in self.params],
        }


@dataclass
class ConnectionConfig:
    name: str
    driver: str = ""
    server: str = ""
    database: str = ""
    auth_type: str = "sql_server"  # uno de config.AUTH_TYPES
    username: str = ""
    password_ref: str = ""
    timeout: int = 5
    active: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "ConnectionConfig":
        return cls(
            name=d.get("name", ""),
            driver=d.get("driver", ""),
            server=d.get("server", ""),
            database=d.get("database", ""),
            auth_type=d.get("auth_type", "sql_server"),
            username=d.get("username", ""),
            password_ref=d.get("password_ref", ""),
            timeout=d.get("timeout", 5),
            active=d.get("active", True),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "driver": self.driver,
            "server": self.server,
            "database": self.database,
            "auth_type": self.auth_type,
            "username": self.username,
            "password_ref": self.password_ref,
            "timeout": self.timeout,
            "active": self.active,
        }
