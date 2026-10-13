from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent, PlatformDiagnostic, WorkerHeartbeat
from apps.audit.selectors import audit_events_visible_to_user, platform_diagnostics_visible_to_user
from apps.audit.services import (
    cleanup_expired_retained_records,
    evaluate_worker_heartbeat,
    record_audit_event,
    record_platform_diagnostic,
    record_worker_heartbeat,
)
from apps.partners.models import Partner
from apps.partners.services import create_partner_with_owner


@pytest.fixture
def audit_context():
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = create_partner_with_owner(
        actor=superuser,
        partner_name="Audit First",
        owner_username="audit-first-owner",
        owner_password="secret",
    )
    second = create_partner_with_owner(
        actor=superuser,
        partner_name="Audit Second",
        owner_username="audit-second-owner",
        owner_password="secret",
    )
    return {
        "superuser": superuser,
        "first": first,
        "second": second,
    }


@pytest.mark.django_db
def test_partner_audit_visibility_is_partner_scoped(audit_context):
    context = audit_context
    first_event = record_audit_event(
        partner=context["first"].partner,
        actor=context["first"].owner,
        event_type="appointment.created",
        message="Appointment created",
    )
    record_audit_event(
        partner=context["second"].partner,
        actor=context["second"].owner,
        event_type="appointment.created",
        message="Other appointment created",
    )

    first_visible = list(audit_events_visible_to_user(context["first"].owner))
    superuser_visible = list(audit_events_visible_to_user(context["superuser"]))

    assert first_visible == [first_event]
    assert len(superuser_visible) == 2


@pytest.mark.django_db
def test_platform_diagnostics_are_superuser_only_and_redacted(audit_context):
    context = audit_context
    diagnostic = record_platform_diagnostic(
        partner=context["first"].partner,
        component=PlatformDiagnostic.Component.TELEGRAM,
        severity=PlatformDiagnostic.Severity.ERROR,
        code="telegram.invalid_token",
        message="Token 8829120637:AAE9wnEkcAWA5B1-Mf_cdL3JEAgcWWVg8ds failed for +380501111111",
        metadata={
            "token": "8829120637:AAE9wnEkcAWA5B1-Mf_cdL3JEAgcWWVg8ds",
            "phone": "+380501111111",
            "message_text": "raw client message",
            "details": "Retry for +380501111111",
        },
    )

    diagnostic.refresh_from_db()

    assert "AAE9wnEkcAWA5B1" not in diagnostic.message
    assert "+380501111111" not in diagnostic.message
    assert diagnostic.metadata["token"] == "***"
    assert diagnostic.metadata["phone"] == "***"
    assert diagnostic.metadata["message_text"] == "***"
    assert "+380501111111" not in diagnostic.metadata["details"]
    assert list(platform_diagnostics_visible_to_user(context["first"].owner)) == []
    assert list(platform_diagnostics_visible_to_user(context["superuser"])) == [diagnostic]


@pytest.mark.django_db
def test_cleanup_deletes_expired_retained_records_but_not_business_records(audit_context):
    context = audit_context
    now = timezone.now()
    audit_event = record_audit_event(
        partner=context["first"].partner,
        event_type="broadcast.completed",
        message="Broadcast completed",
    )
    diagnostic = record_platform_diagnostic(
        component=PlatformDiagnostic.Component.CLEANUP,
        severity=PlatformDiagnostic.Severity.INFO,
        code="cleanup.completed",
        message="old cleanup diagnostic",
    )
    audit_event.retain_until = now - timedelta(days=1)
    audit_event.save(update_fields=["retain_until"])
    diagnostic.retain_until = now - timedelta(days=1)
    diagnostic.save(update_fields=["retain_until"])

    result = cleanup_expired_retained_records(now=now)

    assert result.deleted_by_model["audit.auditevent"] == 1
    assert result.deleted_by_model["audit.platformdiagnostic"] == 1
    assert not AuditEvent.objects.filter(id=audit_event.id).exists()
    assert not PlatformDiagnostic.objects.filter(id=diagnostic.id).exists()
    assert Partner.objects.filter(id=context["first"].partner.id).exists()
    assert PlatformDiagnostic.objects.filter(code="cleanup.deleted_records").exists()


@pytest.mark.django_db
def test_worker_health_thresholds_map_to_ok_warning_critical():
    now = timezone.now()
    ok = record_worker_heartbeat(
        component=PlatformDiagnostic.Component.NOTIFICATIONS_WORKER,
        worker="ok-worker",
        seen_at=now - timedelta(minutes=2),
    )
    warning = record_worker_heartbeat(
        component=PlatformDiagnostic.Component.NOTIFICATIONS_WORKER,
        worker="warning-worker",
        seen_at=now - timedelta(minutes=4),
    )
    critical = record_worker_heartbeat(
        component=PlatformDiagnostic.Component.NOTIFICATIONS_WORKER,
        worker="critical-worker",
        seen_at=now - timedelta(minutes=6),
    )

    assert evaluate_worker_heartbeat(ok, now=now) == WorkerHeartbeat.Status.OK
    assert evaluate_worker_heartbeat(warning, now=now) == WorkerHeartbeat.Status.WARNING
    assert evaluate_worker_heartbeat(critical, now=now) == WorkerHeartbeat.Status.CRITICAL
