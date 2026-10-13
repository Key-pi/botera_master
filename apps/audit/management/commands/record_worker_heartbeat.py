from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.audit.models import PlatformDiagnostic
from apps.audit.services import record_worker_heartbeat


class Command(BaseCommand):
    help = "Record a current worker/component heartbeat."

    def add_arguments(self, parser):
        parser.add_argument("component", choices=PlatformDiagnostic.Component.values)
        parser.add_argument("--queue", default="")
        parser.add_argument("--worker", default="")

    def handle(self, *args, **options):
        heartbeat = record_worker_heartbeat(
            component=options["component"],
            queue=options["queue"],
            worker=options["worker"],
        )
        self.stdout.write(self.style.SUCCESS(f"Heartbeat updated: {heartbeat.id}"))
