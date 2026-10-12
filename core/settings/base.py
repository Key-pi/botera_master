import os
from pathlib import Path

from kombu import Queue

from core.settings.env import database_config, env_bool, env_list, parse_key_ring

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "botera-master-development-secret")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts.apps.AccountsConfig",
    "apps.partners.apps.PartnersConfig",
    "apps.bot.apps.BotConfig",
    "apps.clients.apps.ClientsConfig",
    "apps.catalog.apps.CatalogConfig",
    "apps.schedule.apps.ScheduleConfig",
    "apps.appointments.apps.AppointmentsConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.broadcasts.apps.BroadcastsConfig",
    "apps.messaging.apps.MessagingConfig",
    "apps.finance.apps.FinanceConfig",
    "apps.audit.apps.AuditConfig",
    "apps.integrations.apps.IntegrationsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"
WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {"default": database_config()}

AUTH_USER_MODEL = "accounts.User"

LANGUAGE_CODE = "uk"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

REDIS_URL = env_list("REDIS_URL", ["redis://localhost:6379/0"])[0]
CELERY_BROKER_URL = env_list("CELERY_BROKER_URL", [REDIS_URL])[0]
CELERY_RESULT_BACKEND = env_list("CELERY_RESULT_BACKEND", [REDIS_URL])[0]
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_QUEUES = (
    Queue("default"),
    Queue("notifications"),
    Queue("broadcasts"),
    Queue("maintenance"),
)

BOT_TOKEN_ENCRYPTION_KEYS = parse_key_ring(os.getenv("BOT_TOKEN_ENCRYPTION_KEYS"))
BOT_TOKEN_CURRENT_KEY_ID = os.getenv("BOT_TOKEN_CURRENT_KEY_ID", "")
TOKEN_FINGERPRINT_KEY = os.getenv("TOKEN_FINGERPRINT_KEY", "")
