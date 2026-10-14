import os
from datetime import timedelta
from pathlib import Path

from celery.schedules import crontab
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
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BEAT_SCHEDULE = {
    "dispatch-due-notifications": {
        "task": "notifications.dispatch_due_notifications",
        "schedule": timedelta(seconds=15),
        "options": {"queue": "notifications", "expires": 30},
    },
    "dispatch-queued-conversation-messages": {
        "task": "messaging.dispatch_queued_conversation_messages",
        "schedule": timedelta(seconds=15),
        "options": {"queue": "notifications", "expires": 30},
    },
    "schedule-morning-digests": {
        "task": "notifications.schedule_morning_digests",
        "schedule": timedelta(minutes=1),
        "options": {"queue": "notifications", "expires": 90},
    },
    "dispatch-broadcast-recipient": {
        "task": "broadcasts.dispatch_due_recipients",
        "schedule": timedelta(seconds=1),
        "options": {"queue": "broadcasts", "expires": 5},
    },
    "deliver-pending-integration-events": {
        "task": "integrations.deliver_pending_events",
        "schedule": timedelta(minutes=1),
        "options": {"queue": "default", "expires": 90},
    },
    "refresh-worker-health": {
        "task": "audit.refresh_worker_health",
        "schedule": timedelta(minutes=1),
        "options": {"queue": "maintenance", "expires": 90},
    },
    "cleanup-expired-retained-records": {
        "task": "audit.cleanup_expired_retained_records",
        "schedule": crontab(hour=3, minute=30),
        "options": {"queue": "maintenance"},
    },
    "heartbeat-celery-beat": {
        "task": "audit.heartbeat",
        "schedule": timedelta(minutes=1),
        "kwargs": {"component": "celery_beat"},
        "options": {"queue": "maintenance", "expires": 90},
    },
    "heartbeat-default-worker": {
        "task": "audit.heartbeat",
        "schedule": timedelta(minutes=1),
        "kwargs": {"component": "default_worker"},
        "options": {"queue": "default", "expires": 90},
    },
    "heartbeat-notifications-worker": {
        "task": "audit.heartbeat",
        "schedule": timedelta(minutes=1),
        "kwargs": {"component": "notifications_worker"},
        "options": {"queue": "notifications", "expires": 90},
    },
    "heartbeat-broadcast-worker": {
        "task": "audit.heartbeat",
        "schedule": timedelta(minutes=5),
        "kwargs": {"component": "broadcast_worker"},
        "options": {"queue": "broadcasts", "expires": 360},
    },
}

BOT_TOKEN_ENCRYPTION_KEYS = parse_key_ring(os.getenv("BOT_TOKEN_ENCRYPTION_KEYS"))
BOT_TOKEN_CURRENT_KEY_ID = os.getenv("BOT_TOKEN_CURRENT_KEY_ID", "")
TOKEN_FINGERPRINT_KEY = os.getenv("TOKEN_FINGERPRINT_KEY", "")
INTEGRATION_EVENT_DELIVERY_ENABLED = env_bool("INTEGRATION_EVENT_DELIVERY_ENABLED", False)
