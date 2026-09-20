from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("reports/", views.report_list, name="list"),
    path(
        "reports/<uuid:public_id>/track-timelines/",
        views.report_timeline,
        name="track-timelines",
    ),
    path(
        "reports/<uuid:public_id>/interpolate-positions/",
        views.interpolate_positions,
        name="interpolate-positions",
    ),
]
