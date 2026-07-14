"""Configuración de entorno, rutas y logging. Todo se resuelve una sola vez al importar."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

# ponytail: anchor config/log paths to the entry script's folder (not cwd, and not
# this module's own folder) so behavior doesn't depend on how the app is launched
# (editor Run button, double-clicked .exe, or `python main_dynamic.py` from a
# different folder) nor on how deep this file is nested under app/. Frozen ->
# next to the .exe; unfrozen -> next to whatever __main__ script was run
# (falls back to this file if there's no __main__.__file__, e.g. `python -c`).
if getattr(sys, "frozen", False):
    _entry_path = sys.executable
else:
    _entry_path = getattr(sys.modules["__main__"], "__file__", None) or __file__
BASE_DIR = os.path.dirname(os.path.abspath(_entry_path))

load_dotenv(os.path.join(BASE_DIR, ".env"))

CONNECTIONS_FILE = os.getenv("CONNECTIONS_FILE", os.path.join(BASE_DIR, "db_connections.json"))
QUERIES_FILE = os.getenv("QUERIES_FILE", os.path.join(BASE_DIR, "queries_config.json"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()

try:
    MAX_ROWS = int(os.getenv("MAX_ROWS", "500"))
except ValueError:
    MAX_ROWS = 500

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        RotatingFileHandler(
            os.path.join(BASE_DIR, "app.log"), maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
    ],
)

PARAM_TYPES = ["str", "int", "float", "date"]
AUTH_TYPES = ["windows", "sql_server"]

# Servicio bajo el cual se guardan las contraseñas en el
# Administrador de credenciales de Windows (vía keyring).
KEYRING_SERVICE = "ConsultasDinamicasSQL"
