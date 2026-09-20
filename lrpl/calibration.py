from dataclasses import dataclass
from datetime import timedelta, timezone


@dataclass(frozen=True)
class OffsetSuggestion:
    offset_seconds: int
    matched_count: int
    photo_count: int

    @property
    def match_ratio(self):
        return self.matched_count / self.photo_count if self.photo_count else 0.0


def _naive_utc(value):
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _offset_preference(offset_seconds):
    minutes = abs(offset_seconds) // 60
    if minutes % 60 == 0:
        return 0
    if minutes % 30 == 0:
        return 1
    return 2


def evaluate_time_offset(photo_times, track_intervals, offset_seconds, tolerance_minutes=15):
    photo_times = [_naive_utc(value) for value in photo_times if value is not None]
    intervals = [
        (_naive_utc(start), _naive_utc(finish))
        for start, finish in track_intervals
        if start is not None and finish is not None and start <= finish
    ]
    tolerance = timedelta(minutes=tolerance_minutes)
    offset = timedelta(seconds=offset_seconds)
    matched = sum(
        any(
            start - tolerance <= captured_at + offset <= finish + tolerance
            for start, finish in intervals
        )
        for captured_at in photo_times
    )
    return OffsetSuggestion(
        offset_seconds=offset_seconds,
        matched_count=matched,
        photo_count=len(photo_times),
    )


def suggest_time_offsets(
    photo_times,
    track_intervals,
    *,
    minimum_hours=-14,
    maximum_hours=14,
    step_minutes=15,
    tolerance_minutes=15,
    limit=5,
):
    """Suggest corrections where normalized time equals raw time plus offset."""
    photo_times = [_naive_utc(value) for value in photo_times if value is not None]
    intervals = [
        (_naive_utc(start), _naive_utc(finish))
        for start, finish in track_intervals
        if start is not None and finish is not None and start <= finish
    ]
    if not photo_times or not intervals:
        return []

    first_step = minimum_hours * 60
    last_step = maximum_hours * 60
    suggestions = []
    for offset_minutes in range(first_step, last_step + 1, step_minutes):
        suggestions.append(
            evaluate_time_offset(
                photo_times,
                intervals,
                offset_minutes * 60,
                tolerance_minutes=tolerance_minutes,
            )
        )

    suggestions.sort(
        key=lambda item: (
            -item.matched_count,
            _offset_preference(item.offset_seconds),
            abs(item.offset_seconds),
        )
    )
    return suggestions[:limit]
