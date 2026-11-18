"""Ajustes para ejecutar las pruebas de Django con SQLite en memoria."""

from .settings import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "django-insecure-clave-exclusiva-para-pruebas-locales"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
