from django.urls import path

from . import views

app_name = "tracks"

urlpatterns = [
    path("", views.track_list, name="list"),
    path("tracks/<slug:slug>/", views.track_detail, name="detail"),
    path("tracks/<slug:slug>/geojson/", views.track_geojson, name="geojson"),
]
