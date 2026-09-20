import json
import uuid

from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from tracks.permissions import can_manage

from .authentication import api_user
from .interpolation import DEFAULT_MAX_GAP_SECONDS, INTERPOLATION_ALGORITHM_VERSION
from .models import TravelReport
from .position_services import interpolate_report_items

MAX_INTERPOLATION_ITEMS = 10_000
MAX_GAP_SECONDS = 24 * 60 * 60


def private_report(request, public_id):
    user = api_user(request)
    if user is None:
        return None, JsonResponse({"error": "Требуется аутентификация."}, status=401)
    report = get_object_or_404(
        TravelReport.objects.select_related("owner", "track_group"), public_id=public_id
    )
    if not can_manage(user, report):
        return None, JsonResponse({"error": "Нет доступа к отчёту."}, status=403)
    return report, None


@never_cache
@require_GET
def report_list(request):
    user = api_user(request)
    if user is None:
        return JsonResponse({"error": "Требуется аутентификация."}, status=401)
    reports = TravelReport.objects.select_related("track_group").annotate(
        track_count=Count("track_links")
    )
    if not user.is_superuser:
        reports = reports.filter(owner=user)
    return JsonResponse(
        {
            "schema_version": 1,
            "reports": [
                {
                    "id": str(report.public_id),
                    "name": report.name,
                    "description": report.description,
                    "track_group_name": report.track_group.name if report.track_group else None,
                    "track_count": report.track_count,
                    "updated_at": report.updated_at,
                }
                for report in reports
            ],
        }
    )


@never_cache
@require_GET
def report_timeline(request, public_id):
    report, error = private_report(request, public_id)
    if error:
        return error

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


def _request_items(payload):
    source = payload.get("items")
    if not isinstance(source, list):
        raise TypeError("Поле items должно быть списком.")
    if len(source) > MAX_INTERPOLATION_ITEMS:
        raise ValueError(f"За один запрос разрешено не более {MAX_INTERPOLATION_ITEMS} элементов.")
    result = []
    identifiers = set()
    for raw in source:
        if not isinstance(raw, dict):
            raise TypeError("Каждый элемент должен быть объектом.")
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
            raise ValueError("Каждый элемент должен иметь строковый id длиной до 200 символов.")
        if identifier in identifiers:
            raise ValueError("Значения id внутри запроса не должны повторяться.")
        identifiers.add(identifier)
        item = {"id": identifier}

        captured_at = raw.get("captured_at")
        moment = parse_datetime(captured_at) if isinstance(captured_at, str) else None
        if moment is None or timezone.is_naive(moment):
            item["error"] = "invalid_time"
        else:
            item["captured_at_ms"] = round(moment.timestamp() * 1000)

        track_id = raw.get("track_id")
        if track_id is not None:
            try:
                item["track_id"] = str(uuid.UUID(track_id))
            except (AttributeError, TypeError, ValueError):
                item["error"] = "invalid_track_id"
        result.append(item)
    return result


@never_cache
@require_POST
def interpolate_positions(request, public_id):
    report, error = private_report(request, public_id)
    if error:
        return error
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise TypeError("Тело запроса должно быть объектом.")
        max_gap_seconds = payload.get("max_gap_seconds", DEFAULT_MAX_GAP_SECONDS)
        if (
            isinstance(max_gap_seconds, bool)
            or not isinstance(max_gap_seconds, (int, float))
            or not 0 < max_gap_seconds <= MAX_GAP_SECONDS
        ):
            raise ValueError("max_gap_seconds должен быть от 0 до 86400.")
        items = _request_items(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return JsonResponse({"error": str(exc) or "Неверный JSON."}, status=400)

    return JsonResponse(
        {
            "schema_version": 1,
            "algorithm_version": INTERPOLATION_ALGORITHM_VERSION,
            "max_gap_seconds": max_gap_seconds,
            "results": interpolate_report_items(report, items, max_gap_seconds=max_gap_seconds),
        }
    )
