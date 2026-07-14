"""Base común para los diálogos modales de crear/editar (conexión, consulta).

Solo fija lo que de verdad es idéntico entre `ConnectionEditorDialog` y `QueryEditorDialog`:
el patrón `transient()+grab_set()`, el título "Nueva X"/"Editar X" y la asignación de
`store`/`existing`/`on_saved`/`original_name`. Construir los campos, cargar los datos
existentes y guardar divergen demasiado entre ambos (contraseñas y keyring en uno, SQL y
parámetros en el otro) como para forzarlos a un método de plantilla único — eso terminaría
siendo una abstracción a medias, peor que dejarlos como métodos propios de cada subclase.
"""
import tkinter as tk


class ConfigDialog(tk.Toplevel):
    entity_label = "elemento"  # subclases lo sobreescriben, p.ej. "conexión"
    default_geometry = "600x440"

    def __init__(self, parent, store, existing=None, on_saved=None):
        super().__init__(parent)
        self.store = store
        self.existing = existing
        self.on_saved = on_saved
        self.original_name = existing.name if existing else None

        self.title(f"Editar {self.entity_label}" if existing else f"Nueva {self.entity_label}")
        self.geometry(self.default_geometry)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        if existing:
            self._load_existing()
        self._after_init()

    def _build_ui(self):
        raise NotImplementedError

    def _load_existing(self):
        raise NotImplementedError

    def _after_init(self):
        """Hook opcional para las subclases (p. ej. sincronizar campos tras cargar)."""
