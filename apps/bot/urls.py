from django.urls import path

from apps.bot import views

app_name = "bot"

urlpatterns = [
    path("", views.workspace, name="workspace"),
]
