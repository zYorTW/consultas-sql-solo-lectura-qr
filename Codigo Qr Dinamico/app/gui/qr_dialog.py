"""Diálogo modal: muestra el QR generado, reescalándolo cuando la ventana cambia de tamaño."""
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk


class QrDisplayDialog(tk.Toplevel):
    def __init__(self, parent, image_pil):
        super().__init__(parent)
        self.image_pil = image_pil
        self.photo = None

        self.title("Código QR")
        self.geometry("420x460")
        self.minsize(280, 320)
        self.transient(parent)
        self.grab_set()

        self.qr_label = ttk.Label(self, anchor="center")
        self.qr_label.pack(fill="both", expand=True, padx=15, pady=15)
        ttk.Button(self, text="Cerrar", command=self.destroy).pack(pady=(0, 15))

        self.bind("<Configure>", lambda e: self._render())
        self.after(10, self._render)

    def _render(self):
        self.update_idletasks()
        size = min(self.qr_label.winfo_width(), self.qr_label.winfo_height())
        size = max(150, min(size, 600))
        img_resized = self.image_pil.copy().resize((size, size), Image.Resampling.NEAREST)
        self.photo = ImageTk.PhotoImage(img_resized)
        self.qr_label.config(image=self.photo)
