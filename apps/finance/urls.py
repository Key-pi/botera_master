from django.urls import path

from apps.finance import views

app_name = "finance"

urlpatterns = [
    path("", views.report, name="report"),
]
