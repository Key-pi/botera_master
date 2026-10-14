from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.bot.models import BotInstance
from apps.bot.selectors import primary_master_telegram_contact
from apps.bot.services import check_bot_connection
from apps.partners.models import Partner
from apps.partners.setup import get_partner_setup_status


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    label: str
    passed: bool
    detail: str


class Command(BaseCommand):
    help = "Check whether one configured partner is ready for the Telegram pilot."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--partner-id", required=True, help="Partner UUID.")
        parser.add_argument(
            "--check-telegram",
            action="store_true",
            help="Also call Telegram getMe with the encrypted token stored for the bot.",
        )

    def handle(self, *args, **options) -> None:
        try:
            partner = Partner.objects.get(id=options["partner_id"])
        except (Partner.DoesNotExist, ValidationError, ValueError) as exc:
            raise CommandError("Partner does not exist.") from exc

        bot_instance = BotInstance.objects.filter(partner=partner).first()
        if options["check_telegram"] and bot_instance and bot_instance.has_token:
            check_bot_connection(bot_instance)
            bot_instance.refresh_from_db()

        checks = _readiness_checks(partner=partner, bot_instance=bot_instance)
        self.stdout.write(f"Pilot readiness for {partner.name} ({partner.id})")
        for check in checks:
            marker = "OK" if check.passed else "MISSING"
            line = f"[{marker}] {check.label}: {check.detail}"
            styled_line = self.style.SUCCESS(line) if check.passed else self.style.WARNING(line)
            self.stdout.write(styled_line)

        if not all(check.passed for check in checks):
            raise CommandError("Partner is not ready for the end-to-end Telegram pilot.")

        self.stdout.write(self.style.SUCCESS("Partner is ready for the Telegram pilot."))


def _readiness_checks(
    *,
    partner: Partner,
    bot_instance: BotInstance | None,
) -> tuple[ReadinessCheck, ...]:
    setup = get_partner_setup_status(partner)
    master_contact = primary_master_telegram_contact(partner)
    bot_status = bot_instance.status if bot_instance else "missing"
    bot_error = bot_instance.last_error_code if bot_instance else ""
    identity_known = bool(
        bot_instance
        and bot_instance.telegram_bot_id
        and bot_instance.last_checked_at
    )

    return (
        ReadinessCheck(
            label="partner status",
            passed=partner.status == Partner.Status.ACTIVE,
            detail=partner.status,
        ),
        ReadinessCheck(
            label="minimum setup",
            passed=setup.is_complete,
            detail="complete" if setup.is_complete else ", ".join(setup.missing_steps),
        ),
        ReadinessCheck(
            label="bot instance",
            passed=bool(bot_instance and bot_instance.has_token),
            detail=(
                "encrypted token stored"
                if bot_instance and bot_instance.has_token
                else "missing"
            ),
        ),
        ReadinessCheck(
            label="bot status",
            passed=bot_status == BotInstance.Status.ACTIVE,
            detail=bot_status,
        ),
        ReadinessCheck(
            label="Telegram connection",
            passed=identity_known and not bot_error,
            detail=(
                f"checked as @{bot_instance.telegram_username}"
                if identity_known and bot_instance and bot_instance.telegram_username
                else bot_error or "run with --check-telegram"
            ),
        ),
        ReadinessCheck(
            label="primary master Telegram contact",
            passed=master_contact is not None,
            detail="bound" if master_contact is not None else "open a signed admin link",
        ),
    )
