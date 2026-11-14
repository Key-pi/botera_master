from uuid import uuid4

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.presentation import activity_actor_label
from apps.audit.services import record_audit_event
from apps.partners.services import create_partner_with_owner


@pytest.fixture
def audit_admin_context(client):
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = create_partner_with_owner(
        actor=superuser,
        partner_name="Activity Studio",
        owner_username="activity-owner",
        owner_password="secret",
    )
    second = create_partner_with_owner(
        actor=superuser,
        partner_name="Foreign Studio",
        owner_username="foreign-activity-owner",
        owner_password="secret",
    )
    appointment_event = record_audit_event(
        partner=first.partner,
        actor=first.owner,
        event_type="appointment.status_changed",
        target_type="Appointment",
        target_id=str(uuid4()),
        message="Appointment status changed from requested to confirmed.",
        reason="Confirmed after checking the schedule.",
        payload={
            "old_status": "requested",
            "new_status": "confirmed",
            "phone": "+380501111111",
            "internal_reference": "support-only-value",
        },
    )
    broadcast_event = record_audit_event(
        partner=first.partner,
        event_type="broadcast.completed",
        target_type="BroadcastCampaign",
        target_id=str(uuid4()),
        message="Broadcast campaign completed.",
        payload={
            "status": "completed",
            "sent_count": 12,
            "failed_count": 1,
            "queued_count": 0,
            "skipped_count": 2,
        },
    )
    foreign_event = record_audit_event(
        partner=second.partner,
        actor=second.owner,
        event_type="expense.created",
        message="Foreign expense must stay hidden.",
        payload={"amount": 10000, "currency": "UAH"},
    )
    client.force_login(first.owner)
    return {
        "http_client": client,
        "superuser": superuser,
        "first": first,
        "second": second,
        "appointment_event": appointment_event,
        "broadcast_event": broadcast_event,
        "foreign_event": foreign_event,
    }


@pytest.mark.django_db
def test_partner_activity_log_is_business_friendly_and_tenant_scoped(
    audit_admin_context,
) -> None:
    context = audit_admin_context
    list_url = reverse("admin:audit_auditevent_changelist")

    response = context["http_client"].get(list_url)
    content = response.content.decode()

    assert response.status_code == 200
    assert "Activity Log" in content
    assert "Appointment status changed" in content
    assert "Broadcast completed" in content
    assert "Foreign expense must stay hidden." not in content
    assert "appointment.status_changed" not in content
    assert "payload_masked" not in content
    assert "+380501111111" not in content
    assert "support-only-value" not in content
    assert reverse("admin:audit_auditevent_add") not in content

    filtered = context["http_client"].get(list_url, {"activity_area": "broadcasts"})
    filtered_content = filtered.content.decode()
    assert filtered.status_code == 200
    assert "Broadcast completed" in filtered_content
    assert "Appointment status changed" not in filtered_content


@pytest.mark.django_db
def test_partner_activity_detail_exposes_only_safe_business_context(
    audit_admin_context,
) -> None:
    context = audit_admin_context
    event = context["appointment_event"]
    detail_url = reverse("admin:audit_auditevent_change", args=[event.id])

    response = context["http_client"].get(detail_url)
    content = response.content.decode()

    assert response.status_code == 200
    assert "Activity details" in content
    assert "Previous status" in content
    assert "Requested" in content
    assert "New status" in content
    assert "Confirmed" in content
    assert "Confirmed after checking the schedule." in content
    assert "Open appointment" in content
    assert context["first"].owner.username in content
    assert "Masked technical context" not in content
    assert "internal_reference" not in content
    assert "support-only-value" not in content
    assert context["http_client"].post(detail_url, {}).status_code == 403

    foreign_url = reverse(
        "admin:audit_auditevent_change",
        args=[context["foreign_event"].id],
    )
    assert context["http_client"].get(foreign_url).status_code in {302, 404}


@pytest.mark.django_db
def test_superuser_can_inspect_masked_context_without_raw_sensitive_values(
    audit_admin_context,
) -> None:
    context = audit_admin_context
    context["http_client"].force_login(context["superuser"])
    event = context["appointment_event"]

    detail = context["http_client"].get(reverse("admin:audit_auditevent_change", args=[event.id]))
    detail_content = detail.content.decode()
    listing = context["http_client"].get(reverse("admin:audit_auditevent_changelist"))
    listing_content = listing.content.decode()

    assert detail.status_code == 200
    assert "Masked technical context" in detail_content
    assert "appointment.status_changed" in detail_content
    assert "support-only-value" in detail_content
    assert "+380501111111" not in detail_content
    assert "***" in detail_content
    assert context["first"].partner.name in listing_content
    assert context["second"].partner.name in listing_content


@pytest.mark.django_db
def test_actor_fallback_distinguishes_client_and_system(audit_admin_context) -> None:
    context = audit_admin_context
    client_event = record_audit_event(
        partner=context["first"].partner,
        event_type="appointment.client_rescheduled",
    )
    system_event = record_audit_event(
        partner=context["first"].partner,
        event_type="broadcast.completed",
    )

    assert activity_actor_label(client_event) == "Client"
    assert activity_actor_label(system_event) == "System"
