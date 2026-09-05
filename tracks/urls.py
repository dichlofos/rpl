from django.urls import path

from . import group_views, views

app_name = "tracks"

urlpatterns = [
    path("", views.track_list, name="list"),
    path("tracks/upload/", views.track_upload, name="upload"),
    path("tracks/<uuid:public_id>/", views.track_detail, name="detail"),
    path("tracks/<uuid:public_id>/geojson/", views.track_geojson, name="geojson"),
    path("tracks/<uuid:public_id>/download/", views.track_download, name="download"),
    path("tracks/<uuid:public_id>/edit/", views.track_edit, name="edit"),
    path("tracks/<uuid:public_id>/original/", views.track_original, name="original"),
    path("tracks/<uuid:public_id>/restore/", views.track_restore, name="restore"),
    path("tracks/<uuid:public_id>/group/", views.track_group, name="group"),
    path("groups/", group_views.group_list, name="group-list"),
    path("groups/create/", group_views.group_create, name="group-create"),
    path("groups/<uuid:public_id>/", group_views.group_detail, name="group-detail"),
    path("groups/<uuid:public_id>/geojson/", group_views.group_geojson, name="group-geojson"),
    path("groups/<uuid:public_id>/edit/", group_views.group_edit, name="group-edit"),
    path("groups/<uuid:public_id>/delete/", group_views.group_delete, name="group-delete"),
    path("groups/<uuid:public_id>/add/", group_views.group_add, name="group-add"),
    path(
        "groups/<uuid:public_id>/tracks/<uuid:track_id>/",
        group_views.group_member,
        name="group-member",
    ),
]
