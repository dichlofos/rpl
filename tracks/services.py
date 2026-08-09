import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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

    if not segments:
        raise InvalidGPX("В GPX-файле нет точек маршрута.")

    all_coordinates = [point for segment in segments for point in segment]
    latitudes = [point[1] for point in all_coordinates]
    longitudes = [point[0] for point in all_coordinates]
    uphill, downhill = gpx.get_uphill_downhill()
    started_at = min(times) if times else None
    finished_at = max(times) if times else None

    geometry = (
        {"type": "LineString", "coordinates": segments[0]}
        if len(segments) == 1
        else {"type": "MultiLineString", "coordinates": segments}
    )
    title = next((track.name for track in gpx.tracks if track.name), None)

    return ParsedTrack(
        name=title or Path(filename).stem or "Без названия",
        geojson={
            "type": "Feature",
            "properties": {},
            "geometry": geometry,
        },
        distance_m=gpx.length_3d() or gpx.length_2d(),
        elevation_gain_m=uphill or 0,
        elevation_loss_m=downhill or 0,
        min_elevation_m=min(elevations) if elevations else None,
        max_elevation_m=max(elevations) if elevations else None,
        points_count=len(all_coordinates),
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
