from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.urls import include, path
from django.views.generic import RedirectView


def healthcheck(_: HttpRequest) -> HttpResponse:
    """Lightweight process liveness endpoint for Compose and Caddy."""

    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("health/", healthcheck, name="healthcheck"),
    path("admin/", admin.site.urls),
    path("partner/bot/", include("apps.bot.urls")),
    path("partner/calendar/", include("apps.schedule.urls")),
    path("partner/finance/", include("apps.finance.urls")),
    path("partner/", include("apps.partners.urls")),
    path("", RedirectView.as_view(pattern_name="partners:dashboard", permanent=False)),
]
