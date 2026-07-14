"""Persistencia JSON pura: cargar/guardar/buscar/agregar/actualizar/eliminar.

La validación de contenido vive en `validators.py`, no aquí — este módulo solo sabe leer y
escribir listas de dicts identificados por `name`, de forma atómica.
"""
import json
import logging
import os

from app.exceptions import ConfigError
from app.security import delete_password


class JsonListRepository:
    """Lista de dicts identificados por 'name', persistida en un archivo JSON local."""

    def __init__(self, path, validate):
        self.path = path
        self.validate = validate
        self.items = []
        self.load_error = None
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            self.save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.items = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.exception("Error leyendo %s", self.path)
            self.items = []
            self.load_error = f"No se pudo leer '{self.path}': {e}"

    def save(self):
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    def active_items(self):
        return [i for i in self.items if i.get("active", True)]

    def find_by_name(self, name):
        return next((i for i in self.items if i["name"] == name), None)

    def add(self, item):
        self.validate(item)
        if self.find_by_name(item["name"]):
            raise ConfigError(f"Ya existe un elemento llamado '{item['name']}'.")
        self.items.append(item)
        self.save()

    def update(self, original_name, item):
        self.validate(item)
        idx = next((i for i, it in enumerate(self.items) if it["name"] == original_name), None)
        if idx is None:
            raise ConfigError("El elemento a editar ya no existe.")
        if item["name"] != original_name and self.find_by_name(item["name"]):
            raise ConfigError(f"Ya existe un elemento llamado '{item['name']}'.")
        self.items[idx] = item
        self.save()

    def delete(self, name):
        self.items = [i for i in self.items if i["name"] != name]
        self.save()


class ConnectionRepository(JsonListRepository):
    """Igual que JsonListRepository, pero al eliminar también limpia la credencial guardada."""

    def delete(self, name):
        conn = self.find_by_name(name)
        if conn and conn.get("password_ref"):
            delete_password(conn["password_ref"])
        super().delete(name)
