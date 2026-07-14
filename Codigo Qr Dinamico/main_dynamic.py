"""Punto de entrada. La lógica vive en el paquete app/ (config, security, validators,
repositories, database, qr, utils, models, gui) — ver CLAUDE.md."""
import app.config  # noqa: F401  (importarlo primero fuerza la carga de .env y logging)
from app.gui.app import run

if __name__ == "__main__":
    run()
