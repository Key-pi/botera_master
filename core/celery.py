import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")

app = Celery("botera_master")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
