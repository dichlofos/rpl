from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import gpxpy


@dataclass(frozen=True)
class TimedTrackPoint:
    time: datetime
    longitude: float
    latitude: float
    elevation: float | None


@dataclass(frozen=True)
class TrackTimeline:
    source: Path
    segments: tuple[tuple[TimedTrackPoint, ...], ...]

    @property
    def started_at(self):
        return min(point.time for segment in self.segments for point in segment)

    @property
    def finished_at(self):
        return max(point.time for segment in self.segments for point in segment)


@dataclass(frozen=True)
class InterpolatedPosition:
    longitude: float
    latitude: float
    elevation: float | None
    gap_seconds: float


def utc_datetime(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def read_gpx_timeline(path):
    path = Path(path)
    with path.open("rb") as stream:
        gpx = gpxpy.parse(stream)
    segments = []
    for track in gpx.tracks:
        for source_segment in track.segments:
            points = tuple(
                TimedTrackPoint(
                    time=utc_datetime(point.time),
                    longitude=point.longitude,
                    latitude=point.latitude,
                    elevation=point.elevation,
                )
                for point in source_segment.points
                if point.time is not None
            )
            if points:
                segments.append(points)
    if not segments:
        raise ValueError(f"В GPX нет точек со временем: {path}")
    return TrackTimeline(source=path, segments=tuple(segments))


def interpolate_position(timeline, at, max_gap_seconds=15 * 60):
    at = utc_datetime(at)
    for segment in timeline.segments:
        for left, right in zip(segment, segment[1:]):
            if not left.time <= at <= right.time:
                continue
            gap_seconds = (right.time - left.time).total_seconds()
            if gap_seconds <= 0 or gap_seconds > max_gap_seconds:
                return None
            ratio = (at - left.time).total_seconds() / gap_seconds
            elevation = None
            if left.elevation is not None and right.elevation is not None:
                elevation = left.elevation + (right.elevation - left.elevation) * ratio
            return InterpolatedPosition(
                longitude=left.longitude + (right.longitude - left.longitude) * ratio,
                latitude=left.latitude + (right.latitude - left.latitude) * ratio,
                elevation=elevation,
                gap_seconds=gap_seconds,
            )
    return None
