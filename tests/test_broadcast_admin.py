from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.broadcasts.models import BroadcastCampaign, BroadcastRecipient, BroadcastTemplate
from apps.broadcasts.services import (
    BroadcastDeliveryResult,
    create_broadcast_campaign,
    dispatch_broadcast_recipient,
    queue_due_scheduled_broadcast_campaigns,
    validate_broadcast_content,
)
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.partners.services import create_partner_with_owner


@pytest.fixture
def broadcast_admin_context(client):
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Broadcast Admin Partner",
        owner_username="broadcast-admin-owner",
        owner_password="secret",
    )
    category = create_service_category(
        actor=created.owner,
        partner=created.partner,
        name="Nails",
    )
    service = create_service(
        actor=created.owner,
        partner=created.partner,
        category=category,
        name="Manicure",
        price_amount=50_000,
        duration_minutes=60,
    )
    identity = upsert_telegram_identity(
        telegram_user_id=880001,
        username="broadcast-admin-client",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
    )
    client.force_login(created.owner)
    return {
        "http_client": client,
        "superuser": superuser,
        "created": created,
        "service": service,
        "client_profile": client_profile,
    }


@pytest.mark.django_db
def test_owner_creates_immediate_broadcast_without_json_fields(
    broadcast_admin_context,
) -> None:
    context = broadcast_admin_context
    add_url = reverse("admin:broadcasts_broadcastcampaign_add")

    add_page = context["http_client"].get(add_url)
    response = context["http_client"].post(
        add_url,
        {
            "partner": str(context["created"].partner.id),
            "text": "Book this week",
            "photo": "",
            "audience": "all",
            "service": "",
            "long_absent_days": "",
            "include_book_button": "on",
            "book_button_label": "Записатися",
            "my_appointments_button_label": "Мої записи",
            "external_link_label": "",
            "external_link_url": "",
            "delivery_mode": "now",
            "scheduled_at": "",
            "_save": "Save",
        },
    )

    assert add_page.status_code == 200
    page = add_page.content.decode()
    assert 'name="audience"' in page
    assert 'name="delivery_mode"' in page
    assert 'name="filters"' not in page
    assert 'name="buttons"' not in page
    service_choices = add_page.context["adminform"].form.fields["service"].queryset
    assert list(service_choices) == [context["service"]]
    assert response.status_code == 302

    campaign = BroadcastCampaign.objects.get(partner=context["created"].partner)
    assert campaign.status == BroadcastCampaign.Status.QUEUED
    assert campaign.filters == {}
    assert campaign.buttons == [{"type": "book", "label": "Записатися"}]
    assert campaign.created_by == context["created"].owner
    recipient = BroadcastRecipient.objects.get(campaign=campaign)
    assert recipient.client_profile == context["client_profile"]
    detail_page = context["http_client"].get(
        reverse("admin:broadcasts_broadcastcampaign_change", args=[campaign.id])
    )
    assert detail_page.status_code == 200
    assert detail_page.context["has_change_permission"] is False
    assert "1 pending" in detail_page.content.decode()
    assert AuditEvent.objects.filter(
        partner=context["created"].partner,
        target_id=str(campaign.id),
        event_type="broadcast.created",
    ).exists()
    assert AuditEvent.objects.filter(
        partner=context["created"].partner,
        target_id=str(campaign.id),
        event_type="broadcast.started",
    ).exists()


@pytest.mark.django_db
def test_due_scheduled_broadcast_is_queued_once_and_audited(
    broadcast_admin_context,
) -> None:
    context = broadcast_admin_context
    now = timezone.now()
    due = create_broadcast_campaign(
        actor=context["created"].owner,
        partner=context["created"].partner,
        text="Due campaign",
        scheduled_at=now + timedelta(minutes=1),
    )
    future = create_broadcast_campaign(
        actor=context["created"].owner,
        partner=context["created"].partner,
        text="Future campaign",
        scheduled_at=now + timedelta(hours=1),
    )
    BroadcastCampaign.objects.filter(id=due.id).update(scheduled_at=now - timedelta(minutes=1))

    first_count = queue_due_scheduled_broadcast_campaigns(now=now)
    second_count = queue_due_scheduled_broadcast_campaigns(now=now)

    due.refresh_from_db()
    future.refresh_from_db()
    assert first_count == 1
    assert second_count == 0
    assert due.status == BroadcastCampaign.Status.QUEUED
    assert future.status == BroadcastCampaign.Status.SCHEDULED
    assert BroadcastRecipient.objects.filter(campaign=due).count() == 1
    assert BroadcastRecipient.objects.filter(campaign=future).count() == 0
    assert (
        AuditEvent.objects.filter(
            target_id=str(due.id),
            event_type="broadcast.started",
        ).count()
        == 1
    )

    recipient = BroadcastRecipient.objects.get(campaign=due)
    dispatch_broadcast_recipient(
        recipient=recipient,
        deliver=lambda _recipient: BroadcastDeliveryResult(success=True),
        now=now,
    )
    due.refresh_from_db()
    assert due.status == BroadcastCampaign.Status.COMPLETED
    assert (
        AuditEvent.objects.filter(
            target_id=str(due.id),
            event_type="broadcast.completed",
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_broadcast_service_rejects_unsafe_delivery_content(
    broadcast_admin_context,
) -> None:
    context = broadcast_admin_context

    with pytest.raises(ValidationError, match="must be in the future"):
        create_broadcast_campaign(
            actor=context["created"].owner,
            partner=context["created"].partner,
            text="Past campaign",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
    with pytest.raises(ValidationError, match="1024 characters or shorter"):
        validate_broadcast_content(
            text="x" * 1025,
            photo="telegram-file-id",
            buttons=[],
        )
    with pytest.raises(ValidationError, match=r"valid HTTP\(S\) URL"):
        validate_broadcast_content(
            text="Unsafe link",
            photo="",
            buttons=[
                {
                    "type": "external_link",
                    "label": "Open",
                    "url": "javascript:alert(1)",
                }
            ],
        )


@pytest.mark.django_db
def test_owner_saves_and_reuses_broadcast_template(broadcast_admin_context) -> None:
    context = broadcast_admin_context
    add_url = reverse("admin:broadcasts_broadcasttemplate_add")

    response = context["http_client"].post(
        add_url,
        {
            "partner": str(context["created"].partner.id),
            "name": "Weekly offer",
            "text": "A saved offer",
            "photo": "",
            "include_book_button": "on",
            "book_button_label": "Book now",
            "my_appointments_button_label": "My appointments",
            "external_link_label": "",
            "external_link_url": "",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    template = BroadcastTemplate.objects.get(partner=context["created"].partner)
    assert template.created_by == context["created"].owner
    assert template.buttons == [{"type": "book", "label": "Book now"}]

    campaign_page = context["http_client"].get(
        reverse("admin:broadcasts_broadcastcampaign_add"),
        {"template": str(template.id)},
    )

    assert campaign_page.status_code == 200
    form = campaign_page.context["adminform"].form
    assert form["text"].value() == "A saved offer"
    assert form["include_book_button"].value() is True
    assert form["book_button_label"].value() == "Book now"


@pytest.mark.django_db
def test_broadcast_admin_is_tenant_scoped(broadcast_admin_context) -> None:
    context = broadcast_admin_context
    own = create_broadcast_campaign(
        actor=context["created"].owner,
        partner=context["created"].partner,
        text="Own campaign",
    )
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Foreign Broadcast Partner",
        owner_username="foreign-broadcast-owner",
        owner_password="secret",
    )
    foreign = create_broadcast_campaign(
        actor=other.owner,
        partner=other.partner,
        text="Foreign campaign",
    )

    response = context["http_client"].get(reverse("admin:broadcasts_broadcastcampaign_changelist"))
    foreign_response = context["http_client"].get(
        reverse("admin:broadcasts_broadcastcampaign_change", args=[foreign.id])
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Own campaign" in content
    assert "Foreign campaign" not in content
    assert foreign_response.status_code in {302, 404}
    assert BroadcastCampaign.objects.filter(id=own.id).exists()
    assert BroadcastCampaign.objects.filter(id=foreign.id).exists()
