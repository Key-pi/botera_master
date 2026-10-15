from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ImproperlyConfigured

from apps.accounts.models import User

PARTNER_OWNER_GROUP_NAME = "BoteraMaster partner owners"

# Keep this allowlist deliberately small. New admin surfaces must be reviewed
# for tenant scoping and service-backed mutations before they are added here.
PARTNER_OWNER_PERMISSION_RULES = (
    ("partners", "partner", ("view", "change")),
    ("bot", "botinstance", ("view", "add", "change")),
    ("bot", "partnertelegramcontact", ("view", "change")),
    ("bot", "partnertelegramcontactlink", ("view",)),
    ("bot", "partnertemplateoverride", ("view", "add", "change", "delete")),
    ("catalog", "servicecategory", ("view", "add", "change", "delete")),
    ("catalog", "service", ("view", "add", "change", "delete")),
    ("schedule", "bookingresource", ("view",)),
    ("schedule", "partnerschedulesettings", ("view", "change")),
    ("schedule", "availabilitytemplate", ("view", "add", "change", "delete")),
    (
        "schedule",
        "availabilitytemplatewindow",
        ("view", "add", "change", "delete"),
    ),
    ("schedule", "availabilitywindow", ("view", "add")),
    ("schedule", "blockedtime", ("view", "add", "change", "delete")),
    ("appointments", "appointment", ("view", "add", "change")),
    ("appointments", "appointmentserviceitem", ("view",)),
    ("appointments", "appointmentproposal", ("view",)),
    ("appointments", "appointmentproposaloption", ("view",)),
    ("appointments", "appointmentproposaloptionitem", ("view",)),
    ("clients", "clientprofile", ("view",)),
    ("notifications", "partnernotificationsettings", ("view", "change")),
    ("notifications", "reminderrule", ("view",)),
    ("notifications", "schedulednotification", ("view",)),
    ("notifications", "deliveryattempt", ("view",)),
    ("messaging", "conversationmessage", ("view",)),
    ("finance", "partnerpenaltysettings", ("view", "change")),
    ("audit", "auditevent", ("view",)),
)


def ensure_partner_owner_group() -> Group:
    """Create/update the single MVP owner group from the reviewed allowlist."""

    group, _created = Group.objects.get_or_create(name=PARTNER_OWNER_GROUP_NAME)
    permissions: list[Permission] = []
    missing: list[str] = []
    for app_label, model_name, actions in PARTNER_OWNER_PERMISSION_RULES:
        codenames = [f"{action}_{model_name}" for action in actions]
        found = list(
            Permission.objects.filter(
                content_type__app_label=app_label,
                content_type__model=model_name,
                codename__in=codenames,
            )
        )
        found_by_codename = {permission.codename: permission for permission in found}
        permissions.extend(found)
        missing.extend(
            f"{app_label}.{codename}" for codename in codenames if codename not in found_by_codename
        )

    if missing:
        raise ImproperlyConfigured(
            "Partner owner permissions are unavailable; run migrations first: "
            + ", ".join(sorted(missing))
        )

    group.permissions.set(permissions)
    return group


def grant_partner_owner_admin_access(user: User) -> User:
    """Attach the reviewed MVP admin permissions to one owner account."""

    user.groups.add(ensure_partner_owner_group())
    return user
