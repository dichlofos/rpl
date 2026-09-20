from bisect import bisect_left
from dataclasses import dataclass
from itertools import pairwise
from math import asin, cos, isfinite, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_008.8
INTERPOLATION_ALGORITHM_VERSION = 1
DEFAULT_MAX_GAP_SECONDS = 15 * 60


class InvalidTimeline(ValueError):
    pass


@dataclass(frozen=True)
class TimelinePoint:
    time_ms: int
    longitude: float
    latitude: float
    elevation: float | None


@dataclass(frozen=True)
class TimelineSegment:
    index: int
    points: tuple[TimelinePoint, ...]
    times: tuple[int, ...]


@dataclass(frozen=True)
class InterpolatedPosition:
    longitude: float
    latitude: float
    elevation: float | None
    segment_index: int
    method: str
    left_time_ms: int
    right_time_ms: int
    seconds_from_left: float
    seconds_to_right: float
    gap_seconds: float
    time_radius_seconds: float
    bracket_distance_m: float

    def as_dict(self):
        return {
            "longitude": self.longitude,
            "latitude": self.latitude,
            "elevation": self.elevation,
            "segment_index": self.segment_index,
            "method": self.method,
            "left_time_ms": self.left_time_ms,
            "right_time_ms": self.right_time_ms,
            "seconds_from_left": self.seconds_from_left,
            "seconds_to_right": self.seconds_to_right,
            "gap_seconds": self.gap_seconds,
            "time_radius_seconds": self.time_radius_seconds,
            "bracket_distance_m": self.bracket_distance_m,
        }


@dataclass(frozen=True)
class TimelineFailure:
    reason: str
    details: dict

    def as_dict(self):
        return {"reason": self.reason, **self.details}


@dataclass(frozen=True)
class TimelineLookup:
    positions: tuple[InterpolatedPosition, ...]
    failure: TimelineFailure | None = None


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise InvalidTimeline(f"Invalid {label}.")
    return float(value)


def _point(raw):
    if not isinstance(raw, list) or len(raw) != 4:
        raise InvalidTimeline("Each timeline point must have four fields.")
    time_ms, longitude, latitude, elevation = raw
    if isinstance(time_ms, bool) or not isinstance(time_ms, int):
        raise InvalidTimeline("Timeline timestamps must be integer milliseconds.")
    longitude = _number(longitude, "longitude")
    latitude = _number(latitude, "latitude")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise InvalidTimeline("Timeline coordinates are out of range.")
    if elevation is not None:
        elevation = _number(elevation, "elevation")
    return TimelinePoint(time_ms, longitude, latitude, elevation)


def haversine_distance_m(left, right):
    left_lat = radians(left.latitude)
    right_lat = radians(right.latitude)
    delta_lat = right_lat - left_lat
    delta_lon = radians(right.longitude - left.longitude)
    value = sin(delta_lat / 2) ** 2 + cos(left_lat) * cos(right_lat) * sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(min(1, value)))


def interpolate_longitude(left, right, ratio):
    delta = (right - left + 180) % 360 - 180
    result = left + delta * ratio
    return (result + 180) % 360 - 180


class TimelineIndex:
    def __init__(self, payload):
        if not isinstance(payload, dict):
            raise InvalidTimeline("Timeline must be an object.")
        if payload.get("version") != 1 or payload.get("time_unit") != "ms":
            raise InvalidTimeline("Unsupported timeline format.")
        source_segments = payload.get("segments")
        if not isinstance(source_segments, list):
            raise InvalidTimeline("Timeline segments must be a list.")

        segments = []
        for index, source in enumerate(source_segments):
            if not isinstance(source, list):
                raise InvalidTimeline("Each timeline segment must be a list.")
            points = tuple(_point(item) for item in source)
            times = tuple(point.time_ms for point in points)
            if any(right <= left for left, right in pairwise(times)):
                raise InvalidTimeline("Timeline segment timestamps must increase.")
            if points:
                segments.append(TimelineSegment(index=index, points=points, times=times))
        self.segments = tuple(segments)
        all_times = [time for segment in segments for time in segment.times]
        self.started_at_ms = min(all_times) if all_times else None
        self.finished_at_ms = max(all_times) if all_times else None

    @property
    def points_count(self):
        return sum(len(segment.points) for segment in self.segments)

    def locate(self, at_ms, *, max_gap_seconds=DEFAULT_MAX_GAP_SECONDS):
        if isinstance(at_ms, bool) or not isinstance(at_ms, int):
            raise TypeError("at_ms must be integer milliseconds")
        if (
            isinstance(max_gap_seconds, bool)
            or not isinstance(max_gap_seconds, (int, float))
            or not isfinite(max_gap_seconds)
            or max_gap_seconds <= 0
        ):
            raise ValueError("max_gap_seconds must be positive")
        if not self.segments:
            return TimelineLookup((), TimelineFailure("timeline_empty", {}))

        positions = []
        oversized = []
        max_gap_ms = max_gap_seconds * 1000
        for segment in self.segments:
            position = bisect_left(segment.times, at_ms)
            if position < len(segment.times) and segment.times[position] == at_ms:
                point = segment.points[position]
                positions.append(
                    InterpolatedPosition(
                        longitude=point.longitude,
                        latitude=point.latitude,
                        elevation=point.elevation,
                        segment_index=segment.index,
                        method="exact",
                        left_time_ms=at_ms,
                        right_time_ms=at_ms,
                        seconds_from_left=0,
                        seconds_to_right=0,
                        gap_seconds=0,
                        time_radius_seconds=0,
                        bracket_distance_m=0,
                    )
                )
                continue
            if not 0 < position < len(segment.points):
                continue
            left = segment.points[position - 1]
            right = segment.points[position]
            gap_ms = right.time_ms - left.time_ms
            distance = haversine_distance_m(left, right)
            if gap_ms > max_gap_ms:
                oversized.append((gap_ms, left, right, distance, segment.index))
                continue
            elapsed_ms = at_ms - left.time_ms
            ratio = elapsed_ms / gap_ms
            elevation = None
            if left.elevation is not None and right.elevation is not None:
                elevation = left.elevation + (right.elevation - left.elevation) * ratio
            from_left = elapsed_ms / 1000
            to_right = (right.time_ms - at_ms) / 1000
            positions.append(
                InterpolatedPosition(
                    longitude=interpolate_longitude(left.longitude, right.longitude, ratio),
                    latitude=left.latitude + (right.latitude - left.latitude) * ratio,
                    elevation=elevation,
                    segment_index=segment.index,
                    method="interpolated",
                    left_time_ms=left.time_ms,
                    right_time_ms=right.time_ms,
                    seconds_from_left=from_left,
                    seconds_to_right=to_right,
                    gap_seconds=gap_ms / 1000,
                    time_radius_seconds=max(from_left, to_right),
                    bracket_distance_m=distance,
                )
            )

        if positions:
            return TimelineLookup(tuple(positions))
        if oversized:
            gap_ms, left, right, distance, segment_index = min(oversized, key=lambda item: item[0])
            return TimelineLookup(
                (),
                TimelineFailure(
                    "gap_too_large",
                    {
                        "segment_index": segment_index,
                        "left_time_ms": left.time_ms,
                        "right_time_ms": right.time_ms,
                        "gap_seconds": gap_ms / 1000,
                        "max_gap_seconds": max_gap_seconds,
                        "bracket_distance_m": distance,
                    },
                ),
            )
        if at_ms < self.started_at_ms:
            reason = "before_track"
        elif at_ms > self.finished_at_ms:
            reason = "after_track"
        else:
            reason = "between_segments"
        return TimelineLookup(
            (),
            TimelineFailure(
                reason,
                {
                    "track_started_at_ms": self.started_at_ms,
                    "track_finished_at_ms": self.finished_at_ms,
                },
            ),
        )
