from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from tracks.permissions import can_manage

from .models import TravelReport


@never_cache
@require_GET
def report_timeline(request, public_id):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Требуется аутентификация."}, status=401)
    report = get_object_or_404(
        TravelReport.objects.select_related("owner", "track_group"), public_id=public_id
    )
    if not can_manage(request.user, report):
        return JsonResponse({"error": "Нет доступа к отчёту."}, status=403)

    tracks = []
    links = report.track_links.select_related("track", "track__timeline")
    for link in links:
        track = link.track
        timeline = getattr(track, "timeline", None)
        if timeline is None:
            timeline_status = "missing"
        elif timeline.points_count == 0:
            timeline_status = "empty"
        else:
            timeline_status = "ready"
        tracks.append(
            {
                "id": str(track.public_id),
                "name": track.name,
                "position": link.position,
                "started_at": track.started_at,
                "finished_at": track.finished_at,
                "timeline_status": timeline_status,
                "timed_points_count": timeline.points_count if timeline is not None else None,
                "timeline": timeline.data if timeline is not None else None,
            }
        )
    return JsonResponse(
        {
            "schema_version": 1,
            "report": {
                "id": str(report.public_id),
                "name": report.name,
                "track_group_id": str(report.track_group.public_id)
                if report.track_group_id
                else None,
                "updated_at": report.updated_at,
            },
            "tracks": tracks,
        }
    )
