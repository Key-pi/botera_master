from __future__ import annotations

import os
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import PartnerMembership, User
from apps.catalog.models import Service, ServiceCategory
from apps.catalog.services import create_service, create_service_category
from apps.partners.models import Partner
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import AvailabilityWindow, PartnerScheduleSettings
from apps.schedule.services import (
    create_availability_window,
    get_or_create_default_booking_resource,
    update_schedule_settings,
)
from core.timezones import get_zone

DEFAULT_ADMIN_USERNAME = "platform_admin"
DEFAULT_OWNER_USERNAME = "preview_owner"
DEFAULT_PARTNER_NAME = "Preview Studio"
DEFAULT_DEMO_PASSWORD = "botera12345"
DEMO_WINDOW_DAYS = 7


class Command(BaseCommand):
    help = "Create an idempotent local pilot partner, catalog, schedule, and admin users."

    def handle(self, *args, **options) -> None:
        admin_username = _required_env("DEMO_ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME)
        admin_password = _required_env("DEMO_ADMIN_PASSWORD", DEFAULT_DEMO_PASSWORD)
        owner_username = _required_env("DEMO_OWNER_USERNAME", DEFAULT_OWNER_USERNAME)
        owner_password = _required_env("DEMO_OWNER_PASSWORD", DEFAULT_DEMO_PASSWORD)
        partner_name = _required_env("DEMO_PARTNER_NAME", DEFAULT_PARTNER_NAME)
        if admin_username == owner_username:
            raise CommandError("Demo admin and owner usernames must be different.")

        admin, admin_created = _get_or_create_demo_admin(
            username=admin_username,
            password=admin_password,
        )
        partner, owner, partner_created = _get_or_create_demo_partner(
            admin=admin,
            partner_name=partner_name,
            owner_username=owner_username,
            owner_password=owner_password,
        )
        catalog_created = _ensure_demo_catalog(actor=owner, partner=partner)
        windows_created = _ensure_demo_schedule(actor=owner, partner=partner)

        web_port = os.getenv("WEB_PORT_ON_HOST", "8001").strip() or "8001"
        self.stdout.write(self.style.SUCCESS("Local BoteraMaster pilot demo is ready."))
        self.stdout.write(f"Admin: http://127.0.0.1:{web_port}/admin/")
        self.stdout.write(f"Platform username: {admin.username}")
        self.stdout.write(f"Partner owner username: {owner.username}")
        self.stdout.write("Passwords are read from DEMO_ADMIN_PASSWORD/DEMO_OWNER_PASSWORD.")
        self.stdout.write(f"Partner: {partner.name} ({partner.id})")
        self.stdout.write(
            "Created this run: "
            f"admin={admin_created}, partner={partner_created}, "
            f"catalog_rows={catalog_created}, availability_windows={windows_created}."
        )
        self.stdout.write(
            "Next: add the test token in Admin > Bot instances, check the connection, "
            "bind the master contact, launch the bot, then restart bot-runner."
        )


def _required_env(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise CommandError(f"{name} must not be empty when DEMO_SEED is enabled.")
    return value


def _get_or_create_demo_admin(*, username: str, password: str) -> tuple[User, bool]:
    existing = User.objects.filter(username=username).first()
    if existing is not None:
        if not existing.is_active or not existing.is_staff or not existing.is_superuser:
            raise CommandError(
                f"Existing DEMO_ADMIN_USERNAME {username!r} is not an active superuser."
            )
        return existing, False

    return (
        User.objects.create_superuser(
            username=username,
            password=password,
        ),
        True,
    )


def _get_or_create_demo_partner(
    *,
    admin: User,
    partner_name: str,
    owner_username: str,
    owner_password: str,
) -> tuple[Partner, User, bool]:
    owner = User.objects.filter(username=owner_username).first()
    if owner is not None:
        membership = (
            PartnerMembership.objects.select_related("partner")
            .filter(
                user=owner,
                role=PartnerMembership.Role.OWNER,
                is_active=True,
            )
            .first()
        )
        if membership is None:
            raise CommandError(
                f"Existing DEMO_OWNER_USERNAME {owner_username!r} has no active owner membership."
            )
        return membership.partner, owner, False

    if Partner.objects.filter(name=partner_name).exists():
        raise CommandError(
            f"Partner {partner_name!r} already exists but is not owned by {owner_username!r}."
        )

    created = create_partner_with_owner(
        actor=admin,
        partner_name=partner_name,
        owner_username=owner_username,
        owner_password=owner_password,
    )
    return created.partner, created.owner, True


def _ensure_demo_catalog(*, actor: User, partner: Partner) -> int:
    created_count = 0
    category = ServiceCategory.objects.filter(
        partner=partner,
        name="Нігтьовий сервіс",
    ).first()
    if category is None:
        category = create_service_category(
            actor=actor,
            partner=partner,
            name="Нігтьовий сервіс",
            description="Демонстраційна категорія для локального pilot flow.",
        )
        created_count += 1

    demo_services = (
        ("Манікюр", 60000, 90, "Базова демонстраційна послуга."),
        ("Консультація", 0, 30, "Безкоштовна коротка консультація."),
    )
    for sort_order, (name, price_amount, duration_minutes, description) in enumerate(
        demo_services,
        start=1,
    ):
        if Service.objects.filter(partner=partner, name=name).exists():
            continue
        create_service(
            actor=actor,
            partner=partner,
            category=category,
            name=name,
            description=description,
            price_amount=price_amount,
            duration_minutes=duration_minutes,
            sort_order=sort_order,
        )
        created_count += 1
    return created_count


def _ensure_demo_schedule(*, actor: User, partner: Partner) -> int:
    settings = PartnerScheduleSettings.objects.get(partner=partner)
    if not settings.confirmation_mode:
        update_schedule_settings(
            actor=actor,
            partner=partner,
            confirmation_mode=PartnerScheduleSettings.ConfirmationMode.MANUAL,
        )

    resource = get_or_create_default_booking_resource(partner=partner)
    zone = get_zone(partner.timezone)
    local_today = timezone.now().astimezone(zone).date()
    created_count = 0
    for offset in range(1, DEMO_WINDOW_DAYS + 1):
        day = local_today + timedelta(days=offset)
        start_at = datetime.combine(day, time(hour=10), tzinfo=zone)
        end_at = datetime.combine(day, time(hour=18), tzinfo=zone)
        if AvailabilityWindow.objects.filter(
            partner=partner,
            booking_resource=resource,
            start_at=start_at,
            end_at=end_at,
        ).exists():
            continue
        create_availability_window(
            actor=actor,
            partner=partner,
            booking_resource=resource,
            start_at=start_at,
            end_at=end_at,
        )
        created_count += 1
    return created_count
