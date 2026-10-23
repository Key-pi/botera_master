from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("partner/calendar/", include("apps.schedule.urls")),
    path("partner/", include("apps.partners.urls")),
    path("", RedirectView.as_view(pattern_name="partners:dashboard", permanent=False)),
]
