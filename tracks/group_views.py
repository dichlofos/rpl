from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import GroupAddTrackForm, TrackGroupForm
from .group_services import assign_track, delete_group, move_track
from .models import Track, TrackGroup
from .permissions import can_manage, require_manage

# Keep the list and map colours identical, independently of loading order.
GROUP_COLORS = ("#dc3545", "#0d6efd", "#198754", "#6f42c1", "#b65c00", "#087e8b", "#c02983")


def group_entries(group):
    return [
        {
            "track": link.track,
            "position": index + 1,
            "color": GROUP_COLORS[index % len(GROUP_COLORS)],
        }
        for index, link in enumerate(group.memberships.select_related("track"))
    ]


@require_GET
def group_list(request):
    groups = TrackGroup.objects.annotate(track_count=Count("tracks")).select_related("owner")
    return render(request, "tracks/group_list.html", {"groups": groups})


def render_group(request, group, form=None):
    entries = group_entries(group)
    editable = can_manage(request.user, group)
    return render(
        request,
        "tracks/group_detail.html",
        {
            "group": group,
            "entries": entries,
            "can_edit": editable,
            "add_form": form
            if form is not None
            else GroupAddTrackForm(group=group)
            if editable
            else None,
            "distance_km": sum(entry["track"].distance_m for entry in entries) / 1000,
            "elevation_gain_m": sum(entry["track"].elevation_gain_m for entry in entries),
            "elevation_loss_m": sum(entry["track"].elevation_loss_m for entry in entries),
            "tile_url": settings.OSM_TILE_URL,
            "tile_attribution": settings.OSM_ATTRIBUTION,
            "can_upload": request.user.is_authenticated and group.owner_id == request.user.pk,
        },
    )


@require_GET
def group_detail(request, public_id):
    return render_group(request, get_object_or_404(TrackGroup, public_id=public_id))


@require_GET
def group_geojson(request, public_id):
    group = get_object_or_404(TrackGroup, public_id=public_id)
    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    **entry["track"].geojson,
                    "id": str(entry["track"].public_id),
                    "properties": {
                        "name": entry["track"].name,
                        "url": entry["track"].get_absolute_url(),
                        "position": entry["position"],
                        "color": entry["color"],
                    },
                }
                for entry in group_entries(group)
            ],
        }
    )


@login_required
@require_http_methods(["GET", "POST"])
def group_create(request):
    form = TrackGroupForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        group = form.save(commit=False)
        group.owner = request.user
        group.save()
        return redirect(group)
    return render(
        request,
        "tracks/form.html",
        {
            "title": "Новая группа треков",
            "form": form,
            "back_url": "/groups/",
            "submit_label": "Создать группу",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def group_edit(request, public_id):
    group = get_object_or_404(TrackGroup, public_id=public_id)
    require_manage(request.user, group)
    form = TrackGroupForm(request.POST if request.method == "POST" else None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect(group)
    return render(
        request,
        "tracks/form.html",
        {
            "title": "Редактировать группу",
            "form": form,
            "back_url": group.get_absolute_url(),
            "submit_label": "Сохранить",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def group_delete(request, public_id):
    group = get_object_or_404(TrackGroup, public_id=public_id)
    require_manage(request.user, group)
    if request.method == "POST":
        delete_group(request.user, group.pk)
        messages.success(request, "Группа удалена. Все треки сохранены.")
        return redirect("tracks:group-list")
    return render(request, "tracks/group_delete.html", {"group": group})


@login_required
@require_POST
def group_add(request, public_id):
    group = get_object_or_404(TrackGroup, public_id=public_id)
    require_manage(request.user, group)
    form = GroupAddTrackForm(request.POST, group=group)
    if form.is_valid():
        try:
            assign_track(request.user, form.cleaned_data["track"].pk, group.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect(group)
    return render_group(request, group, form)


@login_required
@require_POST
def group_member(request, public_id, track_id):
    group = get_object_or_404(TrackGroup, public_id=public_id)
    require_manage(request.user, group)
    track = get_object_or_404(Track, public_id=track_id)
    action = request.POST.get("action")
    try:
        if action == "remove":
            assign_track(request.user, track.pk, None, expected_group_id=group.pk)
        else:
            move_track(request.user, group.pk, track.pk, action)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect(group)
