from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.audit.services import cleanup_expired_retained_records


class Command(BaseCommand):
    help = "Delete expired cleanup-eligible records from the approved retention allowlist."

    def handle(self, *args, **options):
        result = cleanup_expired_retained_records()
        for model_label, count in sorted(result.deleted_by_model.items()):
            self.stdout.write(f"{model_label}: {count}")
        self.stdout.write(self.style.SUCCESS(f"Deleted {result.total_deleted} expired row(s)."))

