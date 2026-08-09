from django.urls import path

from . import views

app_name = "tracks"

urlpatterns = [
    path("", views.track_list, name="list"),
    path("tracks/<uuid:public_id>/", views.track_detail, name="detail"),
    path("tracks/<uuid:public_id>/geojson/", views.track_geojson, name="geojson"),
]
