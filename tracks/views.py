import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from regions.models import TrackRegion

from .forms import TrackUploadForm
from .models import Track
from .services import InvalidGPX, edit_gpx


def with_primary_regions(queryset):
    primary_regions = TrackRegion.objects.filter(is_primary=True).select_related("region")
    return queryset.prefetch_related(
        Prefetch("region_links", queryset=primary_regions, to_attr="primary_regions")
    )


def track_list(request):
    return render(
        request,
        "tracks/track_list.html",
        {"tracks": with_primary_regions(Track.objects.all())},
    )


@login_required
def track_upload(request):
    if request.method == "POST":
        form = TrackUploadForm(request.POST, request.FILES)
        if form.is_valid():
            track = form.save(commit=False)
            track.owner = request.user
            track.save()
            messages.success(request, "Трек успешно загружен.")
            return redirect(track)
    else:
        form = TrackUploadForm()
    return render(request, "tracks/track_upload.html", {"form": form})


def track_detail(request, public_id):
    track = get_object_or_404(with_primary_regions(Track.objects.all()), public_id=public_id)
    return render(
        request,
        "tracks/track_detail.html",
        {
            "track": track,
            "tile_url": settings.OSM_TILE_URL,
            "tile_attribution": settings.OSM_ATTRIBUTION,
            "can_edit": request.user.is_authenticated
            and (request.user.is_superuser or track.owner_id == request.user.id),
        },
    )


def track_geojson(request, public_id):
    track = get_object_or_404(Track, public_id=public_id)
    feature = {**track.geojson, "properties": {"name": track.name}}
    return JsonResponse(feature)


@login_required
def track_edit(request, public_id):
    if request.method != "POST":
        return JsonResponse({"error": "Метод не поддерживается."}, status=405)
    track = get_object_or_404(Track, public_id=public_id)
    if not (request.user.is_superuser or track.owner_id == request.user.id):
        return JsonResponse({"error": "Редактировать трек может только его владелец."}, status=403)
    try:
        payload = json.loads(request.body)
        track.gpx_file.open("rb")
        content = track.gpx_file.read()
        edited_content = edit_gpx(
            content,
            payload.get("coordinates"),
            payload.get("start_index"),
            payload.get("end_index"),
        )
    except (json.JSONDecodeError, AttributeError, InvalidGPX) as exc:
        message = str(exc) if isinstance(exc, InvalidGPX) else "Неверные данные редактирования."
        return JsonResponse({"error": message}, status=400)
    finally:
        track.gpx_file.close()

    filename = Path(track.gpx_file.name).name
    track.gpx_file.save(filename, ContentFile(edited_content), save=False)
    track.save()
    return JsonResponse(
        {
            "geojson": {**track.geojson, "properties": {"name": track.name}},
            "stats": {
                "distance_km": round(track.distance_km, 1),
                "elevation_gain_m": round(track.elevation_gain_m),
                "elevation_loss_m": round(track.elevation_loss_m),
                "duration": track.duration_display,
                "points_count": track.points_count,
            },
        }
    )
