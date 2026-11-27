import os

from .settings import *  # noqa: F401,F403


ENVIRONMENT = "staging"
DEBUG = True
ALLOWED_HOSTS = ["*", ".lhr.life", ".devtunnels.ms", ".onrender.com"]
CSRF_TRUSTED_ORIGINS = [
    "https://*.lhr.life",
    "https://*.devtunnels.ms",
    "https://*.onrender.com",
]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

if not all(os.environ.get(name) for name in (
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
)):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db_staging.sqlite3",
        }
    }
