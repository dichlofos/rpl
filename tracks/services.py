import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from math import isfinite
from pathlib import Path
from xml.etree import ElementTree

import gpxpy

MAX_GPX_SIZE = 10 * 1024 * 1024
UNSAFE_XML = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class InvalidGPX(ValueError):
    pass


@dataclass(frozen=True)
class ParsedTrack:
    name: str
    geojson: dict
    distance_m: float
    elevation_gain_m: float
    elevation_loss_m: float
    min_elevation_m: float | None
    max_elevation_m: float | None
    points_count: int
    waypoints_count: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    min_latitude: float
    min_longitude: float
    max_latitude: float
    max_longitude: float


def parse_gpx(content, filename="track.gpx"):
    if not content:
        raise InvalidGPX("GPX-файл пуст.")
    if len(content) > MAX_GPX_SIZE:
        raise InvalidGPX("Размер GPX-файла превышает 10 МБ.")
    if UNSAFE_XML.search(content):
        raise InvalidGPX("DOCTYPE и ENTITY запрещены в GPX-файлах.")

    try:
        gpx = gpxpy.parse(content)
    except Exception as exc:
        raise InvalidGPX("Не удалось разобрать GPX-файл.") from exc

    segments = []
    elevations = []
    times = []
    for track in gpx.tracks:
        for segment in track.segments:
            coordinates = []
            for point in segment.points:
                coordinate = [point.longitude, point.latitude]
                if point.elevation is not None:
                    coordinate.append(point.elevation)
                    elevations.append(point.elevation)
                if point.time is not None:
                    times.append(point.time)
                coordinates.append(coordinate)
            if coordinates:
                segments.append(coordinates)

    waypoints = []
    for index, point in enumerate(gpx.waypoints):
        validate_coordinate([point.longitude, point.latitude])
        waypoints.append(
            {
                "index": index,
                "coordinates": [point.longitude, point.latitude],
                "elevation": point.elevation
                if point.elevation is not None and isfinite(point.elevation)
                else None,
                "name": point.name or "",
                "description": point.description or "",
            }
        )
    if not segments and not waypoints:
        raise InvalidGPX("В GPX-файле нет точек трека или путевых точек (wpt).")

    all_coordinates = [point for segment in segments for point in segment]
    bounds_points = all_coordinates + [point["coordinates"] for point in waypoints]
    for point in all_coordinates:
        validate_coordinate(point[:2])
    latitudes = [point[1] for point in bounds_points]
    longitudes = [point[0] for point in bounds_points]
    uphill, downhill = gpx.get_uphill_downhill()
    started_at = min(times) if times else None
    finished_at = max(times) if times else None

    geometry = route_geometry(segments)
    title = next((track.name for track in gpx.tracks if track.name), None)

    return ParsedTrack(
        name=title or Path(filename).stem or "Без названия",
        geojson={
            "type": "Feature",
            "properties": {},
            "geometry": geometry,
            "waypoints": waypoints,
        },
        distance_m=gpx.length_3d() or gpx.length_2d(),
        elevation_gain_m=uphill or 0,
        elevation_loss_m=downhill or 0,
        min_elevation_m=min(elevations) if elevations else None,
        max_elevation_m=max(elevations) if elevations else None,
        points_count=len(all_coordinates),
        waypoints_count=len(waypoints),
        started_at=started_at,
        finished_at=finished_at,
        duration_s=(finished_at - started_at).total_seconds()
        if started_at and finished_at
        else None,
        min_latitude=min(latitudes),
        min_longitude=min(longitudes),
        max_latitude=max(latitudes),
        max_longitude=max(longitudes),
    )


def route_geometry(segments):
    geometries = [
        {"type": "Point", "coordinates": segment[0]}
        if len(segment) == 1
        else {"type": "LineString", "coordinates": segment}
        for segment in segments
        if segment
    ]
    if not geometries:
        return None
    if len(geometries) == 1:
        return geometries[0]
    if all(item["type"] == "LineString" for item in geometries):
        return {"type": "MultiLineString", "coordinates": segments}
    return {"type": "GeometryCollection", "geometries": geometries}


def line_segments(geometry):
    if not geometry:
        return []
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    return [segment for item in geometry.get("geometries", []) for segment in line_segments(item)]


def validate_coordinate(coordinate):
    if not isinstance(coordinate, list) or len(coordinate) != 2:
        raise InvalidGPX("Каждая точка должна содержать долготу и широту.")
    longitude, latitude = coordinate
    if (
        any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
            for value in coordinate
        )
        or not -180 <= longitude <= 180
        or not -90 <= latitude <= 90
    ):
        raise InvalidGPX("Координаты точки выходят за допустимые границы.")


def edit_waypoints(gpx, waypoints):
    if not isinstance(waypoints, list):
        raise InvalidGPX("Неверный список путевых точек.")
    result = []
    used = set()
    for item in waypoints:
        if not isinstance(item, dict):
            raise InvalidGPX("Неверные данные путевой точки.")
        index = item.get("index")
        if index is None:
            point = gpxpy.gpx.GPXWaypoint()
        else:
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(gpx.waypoints)
                or index in used
            ):
                raise InvalidGPX("Неверный индекс путевой точки.")
            used.add(index)
            point = gpx.waypoints[index]
        coordinate = item.get("coordinates")
        validate_coordinate(coordinate)
        point.longitude, point.latitude = coordinate
        for field in ("name", "description"):
            value = item.get(field, getattr(point, field))
            if value is not None and not isinstance(value, str):
                raise InvalidGPX("Название и описание точки должны быть текстом.")
            setattr(point, field, value)
        elevation = item.get("elevation", point.elevation)
        if elevation is not None and (
            isinstance(elevation, bool)
            or not isinstance(elevation, (int, float))
            or not isfinite(elevation)
        ):
            raise InvalidGPX("Неверная высота путевой точки.")
        point.elevation = elevation
        result.append(point)
    gpx.waypoints = result


def edit_gpx(
    content, coordinates, start_index=None, end_index=None, retained_indices=None, waypoints=None
):
    """Move and trim GPX track points while preserving their metadata."""
    if not content or UNSAFE_XML.search(content):
        raise InvalidGPX("Не удалось прочитать исходный GPX-файл.")
    try:
        gpx = gpxpy.parse(content)
    except Exception as exc:
        raise InvalidGPX("Не удалось прочитать исходный GPX-файл.") from exc

    segments = [segment for track in gpx.tracks for segment in track.segments if segment.points]
    points = [point for segment in segments for point in segment.points]
    if waypoints is not None:
        edit_waypoints(gpx, waypoints)
    if (
        coordinates is None
        and start_index is None
        and end_index is None
        and retained_indices is None
    ):
        coordinates = [[point.longitude, point.latitude] for point in points]
        retained_indices = list(range(len(points)))
    if retained_indices is None:
        if (
            not isinstance(start_index, int)
            or isinstance(start_index, bool)
            or not isinstance(end_index, int)
            or isinstance(end_index, bool)
            or start_index < 0
            or end_index > len(points)
            or start_index >= end_index
        ):
            raise InvalidGPX("Неверные границы обрезки трека.")
        retained_indices = list(range(start_index, end_index))
    elif (
        not isinstance(retained_indices, list)
        or any(
            not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(points)
            for index in retained_indices
        )
        or retained_indices != sorted(set(retained_indices))
    ):
        raise InvalidGPX("Неверный список точек трека.")

    if not isinstance(coordinates, list) or len(coordinates) != len(retained_indices):
        raise InvalidGPX("Число координат не совпадает с числом точек трека.")

    for index, coordinate in zip(retained_indices, coordinates, strict=True):
        validate_coordinate(coordinate)
        longitude, latitude = coordinate
        points[index].longitude = longitude
        points[index].latitude = latitude

    offset = 0
    retained_count = 0
    retained_set = set(retained_indices)
    for segment in segments:
        original_length = len(segment.points)
        segment.points = [
            point
            for local_index, point in enumerate(segment.points)
            if offset + local_index in retained_set
        ]
        retained_count += len(segment.points)
        offset += original_length

    if not retained_count and not gpx.waypoints:
        raise InvalidGPX("Должна остаться хотя бы одна точка трека или путевая точка.")
    # gpxpy loses nested default namespaces and reused prefixes while parsing.
    # Register every namespace under an explicit prefix before serializing extensions.
    for _, (_, uri) in ElementTree.iterparse(BytesIO(content), events=["start-ns"]):
        if uri in {"http://www.topografix.com/GPX/1/0", "http://www.topografix.com/GPX/1/1"}:
            continue
        if uri not in {value for key, value in gpx.nsmap.items() if key != "defaultns"}:
            index = 0
            while f"ext{index}" in gpx.nsmap:
                index += 1
            gpx.nsmap[f"ext{index}"] = uri
    try:
        result = gpx.to_xml().encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise InvalidGPX("Текст путевой точки содержит недопустимые символы.") from exc
    if len(result) > MAX_GPX_SIZE:
        raise InvalidGPX("Размер GPX-файла превышает 10 МБ.")
    try:
        ElementTree.fromstring(result)
    except ElementTree.ParseError as exc:
        raise InvalidGPX("Не удалось сохранить XML путевых точек.") from exc
    return result
