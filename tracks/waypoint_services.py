from copy import deepcopy
from io import BytesIO
from xml.etree import ElementTree

import gpxpy
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction

from .group_services import assign_track
from .models import Track, TrackGroup
from .permissions import require_manage
from .services import InvalidGPX, edit_gpx, parse_gpx

GPX_NAMESPACES = {
    "http://www.topografix.com/GPX/1/0",
    "http://www.topografix.com/GPX/1/1",
}


def _read(track):
    track.gpx_file.open("rb")
    try:
        return track.gpx_file.read()
    finally:
        track.gpx_file.close()


def _add_namespaces(target, content):
    known = {value for key, value in target.nsmap.items() if key != "defaultns"}
    for _, (_, uri) in ElementTree.iterparse(BytesIO(content), events=["start-ns"]):
        if uri in GPX_NAMESPACES or uri in known:
            continue
        index = 0
        while f"ext{index}" in target.nsmap:
            index += 1
        target.nsmap[f"ext{index}"] = uri
        known.add(uri)


@transaction.atomic
def merge_group_waypoints(user, group_id, name):
    group = TrackGroup.objects.select_for_update().get(pk=group_id)
    require_manage(user, group)
    links = list(
        group.memberships.select_for_update()
        .select_related("track")
        .filter(track__deleted_at__isnull=True)
    )
    sources = []
    merged_gpx = gpxpy.gpx.GPX()
    merged_gpx.creator = "RPL"
    for link in links:
        content = _read(link.track)
        try:
            parsed = gpxpy.parse(content)
        except Exception as exc:
            raise ValidationError("Не удалось разобрать один из GPX-файлов группы.") from exc
        if not parsed.waypoints:
            continue
        _add_namespaces(merged_gpx, content)
        merged_gpx.waypoints.extend(deepcopy(parsed.waypoints))
        sources.append((link.track, content))

    if not merged_gpx.waypoints:
        raise ValidationError("В треках группы нет маршрутных точек.")
    try:
        merged_content = merged_gpx.to_xml().encode("utf-8")
        parse_gpx(merged_content, "waypoints.gpx")
    except (InvalidGPX, ValueError, UnicodeError) as exc:
        raise ValidationError(f"Не удалось объединить маршрутные точки: {exc}") from exc

    merged_track = Track.objects.create(
        name=name,
        owner=group.owner,
        gpx_file=ContentFile(merged_content, name="waypoints.gpx"),
    )
    modified_count = 0
    deleted_count = 0
    for track, content in sources:
        if track.points_count:
            track.gpx_file = ContentFile(
                edit_gpx(content, None, waypoints=[]), name="without-waypoints.gpx"
            )
            track.save()
            modified_count += 1
        else:
            track.soft_delete()
            deleted_count += 1
    assign_track(user, merged_track.pk, group.pk)
    return merged_track, modified_count, deleted_count
