from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import cache_control

from .models import Track


def track_list(request):
    return render(request, "tracks/track_list.html", {"tracks": Track.objects.all()})


def track_detail(request, slug):
    track = get_object_or_404(Track, slug=slug)
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
def track_geojson(request, slug):
    track = get_object_or_404(Track, slug=slug)
    feature = {**track.geojson, "properties": {"name": track.name}}
    return JsonResponse(feature)
