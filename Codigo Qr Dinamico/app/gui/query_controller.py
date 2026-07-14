"""Sección 'Consulta guardada': combo + CRUD + vista previa SQL + campos de parámetros.

`on_query_changed` es un hook que `DynamicQueryApp` conecta a
`ExecutionController.clear_result_and_qr`: seleccionar otra consulta invalida el resultado
mostrado, pero limpiar ese resultado es responsabilidad de ExecutionController, no de esta
clase.
"""
import tkinter as tk
from tkinter import ttk, messagebox

from app.gui.query_dialog import QueryEditorDialog
from app.utils import query_allowed_on
from app.validators import convert_param_value


class QueryController:
    def __init__(self, selector_frame, preview_frame, params_frame, root, repo,
                 get_connection_names, get_current_connection_name):
        self.root = root
        self.repo = repo
        self.get_connection_names = get_connection_names
        self.get_current_connection_name = get_current_connection_name
        self.on_query_changed = None  # asignado por DynamicQueryApp tras construir todo
        self.param_widgets = []

        self.query_var = tk.StringVar()
        self.query_combo = ttk.Combobox(selector_frame, textvariable=self.query_var, state="readonly", width=45)
        self.query_combo.grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.query_combo.bind("<<ComboboxSelected>>", lambda e: self.on_query_selected())

        new_btn = ttk.Button(selector_frame, text="Nueva consulta", command=self.new_query)
        new_btn.grid(row=0, column=1, padx=5)
        edit_btn = ttk.Button(selector_frame, text="Editar consulta", command=self.edit_query)
        edit_btn.grid(row=0, column=2, padx=5)
        delete_btn = ttk.Button(selector_frame, text="Eliminar consulta", command=self.delete_query)
        delete_btn.grid(row=0, column=3, padx=5)
        self.buttons = [new_btn, edit_btn, delete_btn]

        self.desc_var = tk.StringVar()
        ttk.Label(selector_frame, textvariable=self.desc_var, foreground="#555").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=5
        )

        self.sql_preview = tk.Text(preview_frame, height=5, wrap="word", state="disabled")
        self.sql_preview.pack(fill="both", expand=True)

        self.params_frame = params_frame

    @property
    def combo(self):
        return self.query_combo

    def selected_name(self):
        return self.query_var.get()

    def refresh_query_list(self, conn_name, select_name=None, auto_select=True):
        queries = self.repo.active_items()
        if conn_name:
            queries = [q for q in queries if query_allowed_on(q, conn_name)]
        names = [q["name"] for q in queries]
        self.query_combo["values"] = names
        if select_name and select_name in names:
            self.query_var.set(select_name)
        elif names and auto_select:
            self.query_var.set(names[0])
        else:
            self.query_var.set("")
        self.on_query_selected()

    def get_selected_query(self):
        name = self.query_var.get()
        return self.repo.find_by_name(name) if name else None

    def on_query_selected(self):
        query = self.get_selected_query()
        if self.on_query_changed:
            self.on_query_changed()
        self.clear_param_widgets()

        self.sql_preview.config(state="normal")
        self.sql_preview.delete("1.0", "end")

        if not query:
            self.desc_var.set("")
            self.sql_preview.config(state="disabled")
            return

        self.desc_var.set(query.get("description", ""))
        self.sql_preview.insert("1.0", query.get("sql", ""))
        self.sql_preview.config(state="disabled")

        self.build_param_widgets(query.get("params", []))

    def clear_param_widgets(self):
        for widget in self.params_frame.winfo_children():
            widget.destroy()
        self.param_widgets = []

    def build_param_widgets(self, params):
        for i, param in enumerate(params):
            ttk.Label(self.params_frame, text=f"{param['label']}:").grid(
                row=i, column=0, sticky="w", padx=5, pady=3
            )
            var = tk.StringVar()
            ttk.Entry(self.params_frame, textvariable=var, width=30).grid(
                row=i, column=1, sticky="w", padx=5, pady=3
            )
            self.param_widgets.append((param, var))

    def get_param_values(self):
        """Puede lanzar ValueError si algún valor no coincide con el tipo declarado."""
        return [convert_param_value(var.get(), param) for param, var in self.param_widgets]

    def new_query(self):
        QueryEditorDialog(
            self.root, self.repo, connection_names=self.get_connection_names(),
            on_saved=lambda name: self.refresh_query_list(self.get_current_connection_name(), select_name=name),
        )

    def edit_query(self):
        query = self.get_selected_query()
        if not query:
            messagebox.showinfo("Editar consulta", "Selecciona una consulta primero.")
            return
        QueryEditorDialog(
            self.root, self.repo, connection_names=self.get_connection_names(), existing=query,
            on_saved=lambda name: self.refresh_query_list(self.get_current_connection_name(), select_name=name),
        )

    def delete_query(self):
        query = self.get_selected_query()
        if not query:
            messagebox.showinfo("Eliminar consulta", "Selecciona una consulta primero.")
            return
        if messagebox.askyesno("Eliminar consulta", f"¿Eliminar la consulta '{query['name']}'?"):
            self.repo.delete(query["name"])
            self.refresh_query_list(self.get_current_connection_name())
