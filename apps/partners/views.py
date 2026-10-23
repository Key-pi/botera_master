from uuid import UUID

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.template.response import TemplateResponse

from apps.partners.selectors import partner_dashboard_snapshot, visible_partners_for_user


@staff_member_required
def dashboard(request):
    """Render the operational landing screen from trusted tenant scope."""

    partners = tuple(visible_partners_for_user(request.user).order_by("name", "id"))
    selected_partner = _selected_partner(request=request, partners=partners)
    context = {
        **admin.site.each_context(request),
        "is_nav_sidebar_enabled": False,
        "title": "Dashboard",
        "partners": partners,
        "snapshot": (
            partner_dashboard_snapshot(partner=selected_partner) if selected_partner else None
        ),
    }

    return TemplateResponse(
        request,
        "partners/dashboard.html",
        context,
    )


def _selected_partner(*, request, partners):
    selected_partner_id = request.GET.get("partner", "").strip()
    if not selected_partner_id:
        return partners[0] if partners else None

    try:
        selected_uuid = UUID(selected_partner_id)
    except (TypeError, ValueError) as exc:
        raise Http404("Partner not found.") from exc

    for partner in partners:
        if partner.id == selected_uuid:
            return partner
    raise Http404("Partner not found.")
