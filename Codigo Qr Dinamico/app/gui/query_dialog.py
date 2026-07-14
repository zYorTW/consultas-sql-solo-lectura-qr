"""Diálogo modal: crear/editar una consulta guardada (SQL, parámetros, conexiones permitidas)."""
import tkinter as tk
from tkinter import ttk, messagebox

from app.config import PARAM_TYPES
from app.exceptions import ConfigError, SQLSecurityError


class QueryEditorDialog(tk.Toplevel):
    def __init__(self, parent, store, connection_names=None, existing=None, on_saved=None):
        super().__init__(parent)
        self.store = store
        self.connection_names = connection_names or []
        self.existing = existing
        self.on_saved = on_saved
        self.original_name = existing["name"] if existing else None
        self.param_rows = []

        self.title("Editar consulta" if existing else "Nueva consulta")
        self.geometry("720x700")
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        if existing:
            self._load_existing()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Nombre:").grid(row=0, column=0, sticky="w", **pad)
        self.name_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.name_var, width=50).grid(row=0, column=1, sticky="we", **pad)

        ttk.Label(top, text="Descripción:").grid(row=1, column=0, sticky="w", **pad)
        self.desc_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.desc_var, width=50).grid(row=1, column=1, sticky="we", **pad)

        self.generate_qr_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Generar QR con el resultado", variable=self.generate_qr_var).grid(
            row=2, column=0, columnspan=2, sticky="w", **pad
        )

        self.active_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Consulta activa", variable=self.active_var).grid(
            row=3, column=0, columnspan=2, sticky="w", **pad
        )

        top.columnconfigure(1, weight=1)

        sql_frame = ttk.LabelFrame(self, text="Sentencia SQL (solo SELECT, sin ';' múltiples)", padding=8)
        sql_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.sql_text = tk.Text(sql_frame, height=8, wrap="word")
        self.sql_text.pack(fill="both", expand=True)

        allowed_frame = ttk.LabelFrame(
            self, text="Conexiones permitidas (ninguna marcada = todas)", padding=8
        )
        allowed_frame.pack(fill="x", padx=10, pady=(0, 8))

        self.allowed_vars = []
        for i, cname in enumerate(self.connection_names):
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(allowed_frame, text=cname, variable=var).grid(
                row=i // 3, column=i % 3, sticky="w", padx=4, pady=2
            )
            self.allowed_vars.append((cname, var))
        if not self.connection_names:
            ttk.Label(allowed_frame, text="No hay conexiones definidas todavía.").pack(anchor="w")

        params_frame = ttk.LabelFrame(
            self, text="Parámetros (mismo orden que los '?' de la consulta)", padding=8
        )
        params_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self.params_container = ttk.Frame(params_frame)
        self.params_container.pack(fill="both", expand=True)

        ttk.Button(params_frame, text="+ Agregar parámetro", command=self.add_param_row).pack(
            anchor="w", pady=(6, 0)
        )

        btns = ttk.Frame(self, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="Guardar", command=self.on_save).pack(side="right", padx=4)
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side="right", padx=4)

    def add_param_row(self, param=None):
        row_frame = ttk.Frame(self.params_container)
        row_frame.pack(fill="x", pady=2)

        name_var = tk.StringVar(value=(param or {}).get("name", ""))
        label_var = tk.StringVar(value=(param or {}).get("label", ""))
        type_var = tk.StringVar(value=(param or {}).get("type", "str"))
        required_var = tk.BooleanVar(value=(param or {}).get("required", True))

        ttk.Entry(row_frame, textvariable=name_var, width=16).pack(side="left", padx=2)
        ttk.Entry(row_frame, textvariable=label_var, width=22).pack(side="left", padx=2)
        ttk.Combobox(
            row_frame, textvariable=type_var, values=PARAM_TYPES, width=8, state="readonly"
        ).pack(side="left", padx=2)
        ttk.Checkbutton(row_frame, text="Requerido", variable=required_var).pack(side="left", padx=2)

        row = {"frame": row_frame, "name": name_var, "label": label_var, "type": type_var, "required": required_var}

        def remove():
            row_frame.destroy()
            self.param_rows.remove(row)

        ttk.Button(row_frame, text="Quitar", command=remove).pack(side="left", padx=2)

        self.param_rows.append(row)

    def _load_existing(self):
        self.name_var.set(self.existing["name"])
        self.desc_var.set(self.existing.get("description", ""))
        self.sql_text.insert("1.0", self.existing.get("sql", ""))
        self.generate_qr_var.set(self.existing.get("generate_qr", True))
        self.active_var.set(self.existing.get("active", True))
        existing_allowed = self.existing.get("allowed_connections") or []
        for cname, var in self.allowed_vars:
            var.set(cname in existing_allowed)
        for p in self.existing.get("params", []):
            self.add_param_row(p)

    def on_save(self):
        checked = [cname for cname, var in self.allowed_vars if var.get()]
        # Conservar nombres permitidos que apunten a conexiones hoy inexistentes
        # (p. ej. definidas en otro equipo) en vez de perderlos silenciosamente.
        known = [cname for cname, _ in self.allowed_vars]
        existing_allowed = (self.existing or {}).get("allowed_connections") or []
        allowed = checked + [n for n in existing_allowed if n not in known]

        query = {
            "name": self.name_var.get().strip(),
            "description": self.desc_var.get().strip(),
            "sql": self.sql_text.get("1.0", "end").strip(),
            "generate_qr": self.generate_qr_var.get(),
            "active": self.active_var.get(),
            "allowed_connections": allowed,
            "params": [
                {
                    "name": r["name"].get().strip(),
                    "label": r["label"].get().strip() or r["name"].get().strip(),
                    "type": r["type"].get(),
                    "required": r["required"].get(),
                }
                for r in self.param_rows
            ],
        }

        try:
            if self.original_name:
                self.store.update(self.original_name, query)
            else:
                self.store.add(query)
        except (ConfigError, SQLSecurityError) as e:
            messagebox.showerror("No se puede guardar", str(e), parent=self)
            return

        if self.on_saved:
            self.on_saved(query["name"])
        self.destroy()
