from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import cache_control

from .forms import TrackUploadForm
from .models import Track


def track_list(request):
    return render(request, "tracks/track_list.html", {"tracks": Track.objects.all()})


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
    track = get_object_or_404(Track, public_id=public_id)
    return render(
        request,
        "tracks/track_detail.html",
        {
            "track": track,
            "tile_url": settings.OSM_TILE_URL,
            "tile_attribution": settings.OSM_ATTRIBUTION,
        },
    )


@cache_control(public=True, max_age=3600)
def track_geojson(request, public_id):
    track = get_object_or_404(Track, public_id=public_id)
    feature = {**track.geojson, "properties": {"name": track.name}}
    return JsonResponse(feature)
