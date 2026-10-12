from django.contrib.auth.decorators import login_required
from django.template.response import TemplateResponse

from apps.partners.selectors import visible_partners_for_user


@login_required
def dashboard(request):
    """Minimal partner dashboard placeholder backed by tenant-scoped reads."""

    return TemplateResponse(
        request,
        "partners/dashboard.html",
        {"partners": visible_partners_for_user(request.user)},
    )
