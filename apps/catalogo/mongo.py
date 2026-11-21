from functools import lru_cache

from django.conf import settings
from pymongo import MongoClient


@lru_cache(maxsize=1)
def obtener_cliente():
    """Reutiliza un cliente de MongoDB por proceso."""
    return MongoClient(
        settings.MONGO_URI,
        serverSelectionTimeoutMS=10000,
        connectTimeoutMS=10000,
        tz_aware=True,
    )


def obtener_base():
    """Obtiene la base configurada en settings.py."""
    return obtener_cliente()[settings.MONGO_DB_NAME]