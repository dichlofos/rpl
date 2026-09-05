import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Prefetch
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_http_methods

from regions.models import TrackRegion

from .archive_services import restore_original
from .forms import GroupChoiceForm, TrackUploadForm
from .group_services import assign_track
from .models import Track
from .permissions import can_manage, require_manage
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
        form = TrackUploadForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            try:
                with transaction.atomic():
                    track = form.save(commit=False)
                    track.owner = request.user
                    track.save()
                    group = form.cleaned_data["group"]
                    if group:
                        assign_track(request.user, track.pk, group.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Трек успешно загружен.")
                return redirect(group if group else track)
    else:
        form = TrackUploadForm(user=request.user, initial={"group": request.GET.get("group")})
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
            "can_edit": can_manage(request.user, track),
            "groups": track.groups.all(),
        },
    )


def track_geojson(request, public_id):
    track = get_object_or_404(Track, public_id=public_id)
    feature = {**track.geojson, "properties": {"name": track.name}}
    return JsonResponse(feature)


@require_GET
def track_download(request, public_id):
    track = get_object_or_404(Track, public_id=public_id)
    filename = slugify(track.name, allow_unicode=True) or f"track-{track.public_id}"
    return FileResponse(
        track.gpx_file.open("rb"),
        as_attachment=True,
        filename=f"{filename}.gpx",
        content_type="application/gpx+xml",
    )


@require_GET
def track_original(request, public_id):
    track = get_object_or_404(Track, public_id=public_id)
    filename = slugify(track.name, allow_unicode=True) or str(track.public_id)
    return FileResponse(
        track.original_gpx_file.open("rb"),
        as_attachment=True,
        filename=f"{filename}-original.gpx",
        content_type="application/gpx+xml",
    )


@login_required
@require_http_methods(["GET", "POST"])
def track_restore(request, public_id):
    track = get_object_or_404(Track, public_id=public_id)
    require_manage(request.user, track)
    if request.method == "POST":
        try:
            restore_original(request.user, track.pk)
        except (OSError, ValidationError):
            messages.error(request, "Не удалось восстановить архив. Текущий трек сохранён.")
        else:
            messages.success(request, "Содержимое трека восстановлено из архива.")
            return redirect(track)
    return render(request, "tracks/track_restore.html", {"track": track})


@login_required
@require_http_methods(["GET", "POST"])
def track_group(request, public_id):
    track = get_object_or_404(Track, public_id=public_id)
    require_manage(request.user, track)
    current = track.groups.first()
    form = GroupChoiceForm(
        request.POST if request.method == "POST" else None,
        owner_id=track.owner_id,
        initial={"group": current.public_id if current else None},
    )
    if request.method == "POST" and form.is_valid():
        group = form.cleaned_data["group"]
        try:
            assign_track(request.user, track.pk, group.pk if group else None)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Группа трека изменена.")
            return redirect(track)
    return render(
        request,
        "tracks/form.html",
        {
            "form": form,
            "title": f"Группа трека «{track}»",
            "back_url": track.get_absolute_url(),
            "submit_label": "Сохранить",
            "help_text": "Выбор другой группы переносит трек из текущей.",
        },
    )


@login_required
@transaction.atomic
def track_edit(request, public_id):
    if request.method != "POST":
        return JsonResponse({"error": "Метод не поддерживается."}, status=405)
    track = get_object_or_404(Track.objects.select_for_update(), public_id=public_id)
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
            payload.get("retained_indices"),
        )
    except (json.JSONDecodeError, AttributeError, InvalidGPX) as exc:
        message = str(exc) if isinstance(exc, InvalidGPX) else "Неверные данные редактирования."
        return JsonResponse({"error": message}, status=400)
    finally:
        track.gpx_file.close()

    track.gpx_file = ContentFile(edited_content, name=Path(track.gpx_file.name).name)
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
