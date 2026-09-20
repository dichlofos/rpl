from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path(
        "reports/<uuid:public_id>/track-timelines/",
        views.report_timeline,
        name="track-timelines",
    ),
]
