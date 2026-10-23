from django.urls import path

from apps.schedule import views

app_name = "schedule"

urlpatterns = [
    path("", views.calendar, name="calendar"),
    path("conflicts/", views.conflict_list, name="conflicts"),
]
