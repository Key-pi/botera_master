from django.core.management.base import BaseCommand

from apps.accounts.models import PartnerMembership, User
from apps.accounts.permissions import ensure_partner_owner_group


class Command(BaseCommand):
    help = "Synchronize the reviewed partner-owner admin group and existing owner accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many active owner accounts would be synchronized.",
        )

    def handle(self, *args, **options):
        owner_ids = PartnerMembership.objects.filter(
            role=PartnerMembership.Role.OWNER,
            is_active=True,
            user__is_active=True,
        ).values_list("user_id", flat=True)
        owners = User.objects.filter(id__in=owner_ids).distinct()
        owner_count = owners.count()
        if options["dry_run"]:
            self.stdout.write(f"Would synchronize {owner_count} active partner owner account(s).")
            return

        group = ensure_partner_owner_group()
        User.objects.filter(id__in=owner_ids, is_staff=False).update(is_staff=True)
        group.user_set.set(owners)
        self.stdout.write(
            self.style.SUCCESS(f"Synchronized {owner_count} active partner owner account(s).")
        )
