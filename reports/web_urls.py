from django.urls import path

from . import web_views

app_name = "journeys"

urlpatterns = [
    path("", web_views.journey_list, name="list"),
    path("create/", web_views.journey_create, name="create"),
    path("<uuid:public_id>/", web_views.journey_detail, name="detail"),
    path("<uuid:public_id>/edit/", web_views.journey_edit, name="edit"),
    path("<uuid:public_id>/tracks/", web_views.journey_tracks, name="tracks"),
]
