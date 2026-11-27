# Conexiones independientes para las pruebas de integración de FYAG.

import os
from urllib.parse import unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F401,F403


try:
    conexion = urlsplit(os.environ["QA_DATABASE_URL"])
    host_qa = conexion.hostname or ""
    puerto_qa = conexion.port or 5432
except (KeyError, ValueError) as error:
    raise ImproperlyConfigured("Configura QA_DATABASE_URL con la conexión directa de Neon de pruebas.") from error

if (
    conexion.scheme not in {"postgres", "postgresql"}
    or not host_qa.endswith(".neon.tech")
    or "-pooler." in host_qa
    or host_qa == DATABASES["default"]["HOST"].lower().replace("-pooler.", ".")
    or not (conexion.username and conexion.password and conexion.path.strip("/"))
):
    raise ImproperlyConfigured("QA_DATABASE_URL debe apuntar a una rama de pruebas distinta de la conexión habitual.")

QA_NEON_BRANCH_ID = os.environ.get("QA_NEON_BRANCH_ID", "")
base_mongo_qa = os.environ.get("QA_MONGO_DB_NAME", "")
if (
    not QA_NEON_BRANCH_ID.startswith("br-")
    or not base_mongo_qa.endswith("_qa")
    or base_mongo_qa.lower() == MONGO_DB_NAME.lower()
):
    raise ImproperlyConfigured("Indica una rama QA y una base MongoDB diferente de la habitual que termine en _qa.")
MONGO_DB_NAME = base_mongo_qa

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(conexion.path.lstrip("/")),
        "USER": unquote(conexion.username),
        "PASSWORD": unquote(conexion.password),
        "HOST": host_qa,
        "PORT": puerto_qa,
        "OPTIONS": {"sslmode": "require", "connect_timeout": 12},
    },
}
