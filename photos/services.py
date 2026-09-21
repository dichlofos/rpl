import math
import uuid
from pathlib import PurePosixPath

from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import PhotoAsset, PhotoBatch, PhotoPlacement, generate_photo_key

MAX_PHOTOS_PER_REQUEST = 10_000


def _uuid(value, field):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Некорректный {field}.") from exc


def _integer(value, field, *, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"Некорректное поле {field}.")
    if maximum is not None and value > maximum:
        raise ValidationError(f"Некорректное поле {field}.")
    return value


def _number(value, field, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValidationError(f"Некорректное поле {field}.")
    return float(value)


def _text(value, field, maximum, *, blank=False):
    if not isinstance(value, str) or len(value) > maximum or (not blank and not value):
        raise ValidationError(f"Некорректное поле {field}.")
    return value


def _boolean(value, field):
    if not isinstance(value, bool):
        raise ValidationError(f"Некорректное поле {field}.")
    return value


def _relative_path(value):
    value = _text(value, "relative_path", 1024)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValidationError("Некорректный относительный путь фотографии.")
    return value


def _normalized_time(value):
    if value is None:
        return None
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or timezone.is_naive(parsed):
        raise ValidationError("Нормализованное время должно содержать часовой пояс.")
    return parsed


def _placement(value, selected_tracks):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidationError("Некорректная геопривязка.")
    source = value.get("source")
    if source not in PhotoPlacement.Source.values:
        raise ValidationError("Неизвестный источник геопривязки.")
    longitude = _number(value.get("longitude"), "longitude", -180, 180)
    latitude = _number(value.get("latitude"), "latitude", -90, 90)
    elevation = value.get("elevation")
    if elevation is not None:
        elevation = _number(elevation, "elevation", -20_000, 100_000)
    track = None
    if source == PhotoPlacement.Source.TRACK:
        track_id = str(_uuid(value.get("track_id"), "track_id"))
        track = selected_tracks.get(track_id)
        if track is None:
            raise ValidationError("Трек геопривязки не входит в отчёт.")
    details = value.get("details", {})
    if not isinstance(details, dict):
        raise ValidationError("Детали геопривязки должны быть объектом.")
    algorithm_version = value.get("algorithm_version")
    if algorithm_version is not None:
        algorithm_version = _integer(
            algorithm_version, "algorithm_version", minimum=1, maximum=1_000_000
        )
    return {
        "point": Point(longitude, latitude, srid=4326),
        "elevation": elevation,
        "source": source,
        "track": track,
        "algorithm_version": algorithm_version,
        "details": details,
        "confirmed": _boolean(value.get("confirmed", False), "confirmed"),
    }


def validate_payload(report, payload):
    if not isinstance(payload, dict):
        raise ValidationError("Тело запроса должно быть объектом.")
    client_id = _uuid(payload.get("client_id"), "client_id пачки")
    name = _text(payload.get("name"), "name", 200)
    calibration = payload.get("calibration", {})
    if not isinstance(calibration, dict):
        raise ValidationError("Калибровка должна быть объектом.")
    source = payload.get("photos")
    if not isinstance(source, list) or len(source) > MAX_PHOTOS_PER_REQUEST:
        raise ValidationError(f"Допустимо не более {MAX_PHOTOS_PER_REQUEST} фотографий.")
    selected_tracks = {
        str(link.track.public_id): link.track for link in report.track_links.select_related("track")
    }
    photos = []
    identifiers = set()
    for item in source:
        if not isinstance(item, dict):
            raise ValidationError("Данные фотографии должны быть объектом.")
        photo_id = _uuid(item.get("client_id"), "client_id фотографии")
        if photo_id in identifiers:
            raise ValidationError("client_id фотографий не должны повторяться.")
        identifiers.add(photo_id)
        raw_time = item.get("captured_at_raw") or ""
        logical_day = (
            _integer(item["logical_day"], "logical_day", minimum=1, maximum=99)
            if item.get("logical_day") is not None
            else None
        )
        day_confirmed = _boolean(item.get("day_confirmed", False), "day_confirmed")
        if day_confirmed and logical_day is None:
            raise ValidationError("Нельзя подтвердить фотографию без логического дня.")
        photos.append(
            {
                "client_id": photo_id,
                "relative_path": _relative_path(item.get("relative_path")),
                "file_size": _integer(item.get("file_size"), "file_size"),
                "file_mtime_ns": _integer(item.get("file_mtime_ns"), "file_mtime_ns"),
                "captured_at_raw": _text(raw_time, "captured_at_raw", 64, blank=True),
                "timezone_explicit": _boolean(
                    item.get("timezone_explicit", False), "timezone_explicit"
                ),
                "time_offset_seconds": _integer(
                    item.get("time_offset_seconds", 0),
                    "time_offset_seconds",
                    minimum=-86400,
                    maximum=86400,
                ),
                "captured_at_normalized": _normalized_time(item.get("captured_at_normalized")),
                "logical_day": logical_day,
                "day_confirmed": day_confirmed,
                "camera": _text(item.get("camera", ""), "camera", 300, blank=True),
                "content_sha256": _text(
                    item.get("content_sha256", ""), "content_sha256", 64, blank=True
                ),
                "placement": _placement(item.get("placement"), selected_tracks),
            }
        )
    return {"client_id": client_id, "name": name, "calibration": calibration, "photos": photos}


def _new_photo_key(reserved):
    while True:
        value = generate_photo_key()
        if value not in reserved and not PhotoAsset.objects.filter(photo_key=value).exists():
            reserved.add(value)
            return value


@transaction.atomic
def register_photo_batch(user, report, payload):
    data = validate_payload(report, payload)
    batch = (
        PhotoBatch.objects.select_for_update()
        .filter(owner=user, client_id=data["client_id"])
        .first()
    )
    if batch is None:
        batch = PhotoBatch.objects.create(
            owner=user,
            report=report,
            client_id=data["client_id"],
            name=data["name"],
            calibration=data["calibration"],
        )
    elif batch.report_id != report.pk:
        raise ValidationError("Локальная пачка уже привязана к другому отчёту.")
    else:
        batch.name = data["name"]
        batch.calibration = data["calibration"]
        batch.save(update_fields=["name", "calibration", "updated_at"])

    existing = {
        item.client_id: item
        for item in PhotoAsset.objects.filter(
            batch=batch, client_id__in=[photo["client_id"] for photo in data["photos"]]
        )
    }
    reserved = set()
    created = []
    updated = []
    asset_fields = [
        "relative_path",
        "file_size",
        "file_mtime_ns",
        "captured_at_raw",
        "timezone_explicit",
        "time_offset_seconds",
        "captured_at_normalized",
        "logical_day",
        "day_confirmed",
        "camera",
        "content_sha256",
        "updated_at",
    ]
    now = timezone.now()
    for photo in data["photos"]:
        values = {key: value for key, value in photo.items() if key != "placement"}
        asset = existing.get(photo["client_id"])
        if asset is None:
            asset = PhotoAsset(
                batch=batch,
                photo_key=_new_photo_key(reserved),
                **values,
            )
            created.append(asset)
        else:
            for key, value in values.items():
                setattr(asset, key, value)
            asset.updated_at = now
            updated.append(asset)
    PhotoAsset.objects.bulk_create(created)
    if updated:
        PhotoAsset.objects.bulk_update(updated, asset_fields)

    assets = {
        item.client_id: item
        for item in PhotoAsset.objects.filter(
            batch=batch, client_id__in=[photo["client_id"] for photo in data["photos"]]
        )
    }
    placement_photo_ids = []
    for photo in data["photos"]:
        asset = assets[photo["client_id"]]
        placement = photo["placement"]
        if placement is None:
            continue
        placement_photo_ids.append(asset.pk)
        PhotoPlacement.objects.update_or_create(photo=asset, defaults=placement)
    PhotoPlacement.objects.filter(
        photo__batch=batch,
        photo__client_id__in=[photo["client_id"] for photo in data["photos"]],
    ).exclude(photo_id__in=placement_photo_ids).delete()

    return {
        "batch": {"id": str(batch.public_id), "client_id": str(batch.client_id)},
        "photos": [
            {
                "id": str(assets[photo["client_id"]].public_id),
                "client_id": str(photo["client_id"]),
                "photo_key": assets[photo["client_id"]].photo_key,
            }
            for photo in data["photos"]
        ],
    }
