"""Configuración de entorno, rutas y logging. Todo se resuelve una sola vez al importar."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

# ponytail: anchor config/log paths to the project root (not cwd) so behavior doesn't
# depend on how the app is launched (editor Run button, double-clicked .exe,
# `python main_dynamic.py`, or `python -m tests.test_x` from a subfolder). Frozen ->
# next to the .exe. Unfrozen -> two levels up from this file's own location, since
# this file always lives at <project root>/app/config.py; anchoring to __main__'s
# script instead would break as soon as __main__ isn't the project root itself
# (e.g. running a test under tests/ via `-m`, where __main__.__file__ points there).
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
