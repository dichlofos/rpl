from dataclasses import dataclass

from .interpolation import InvalidTimeline, TimelineIndex


@dataclass(frozen=True)
class ReportTrackIndex:
    track_id: str
    name: str
    position: int
    timeline: TimelineIndex | None
    unavailable_reason: str | None


def build_report_track_indexes(report):
    indexes = []
    for link in report.track_links.select_related("track", "track__timeline"):
        track = link.track
        stored = getattr(track, "timeline", None)
        if stored is None:
            timeline = None
            reason = "timeline_missing"
        else:
            try:
                timeline = TimelineIndex(stored.data)
            except InvalidTimeline:
                timeline = None
                reason = "invalid_timeline"
            else:
                reason = None
        indexes.append(
            ReportTrackIndex(
                track_id=str(track.public_id),
                name=track.name,
                position=link.position,
                timeline=timeline,
                unavailable_reason=reason,
            )
        )
    return tuple(indexes)


def _track_result(index, lookup):
    base = {
        "track_id": index.track_id,
        "track_name": index.name,
        "track_position": index.position,
    }
    if lookup.positions:
        return [base | position.as_dict() for position in lookup.positions], None
    return [], base | lookup.failure.as_dict()


def interpolate_report_items(report, items, *, max_gap_seconds):
    indexes = build_report_track_indexes(report)
    by_id = {item.track_id: item for item in indexes}
    results = []
    for item in items:
        base = {"id": item["id"]}
        if item.get("error"):
            results.append(base | {"status": "invalid", "reason": item["error"]})
            continue

        requested_track_id = item.get("track_id")
        if requested_track_id:
            requested = by_id.get(requested_track_id)
            if requested is None:
                results.append(base | {"status": "unmatched", "reason": "track_not_selected"})
                continue
            selected_indexes = (requested,)
        else:
            selected_indexes = indexes
        if not selected_indexes:
            results.append(base | {"status": "unmatched", "reason": "no_selected_tracks"})
            continue

        candidates = []
        failures = []
        for index in selected_indexes:
            if index.unavailable_reason:
                failures.append(
                    {
                        "track_id": index.track_id,
                        "track_name": index.name,
                        "track_position": index.position,
                        "reason": index.unavailable_reason,
                    }
                )
                continue
            lookup = index.timeline.locate(item["captured_at_ms"], max_gap_seconds=max_gap_seconds)
            positions, failure = _track_result(index, lookup)
            candidates.extend(positions)
            if failure:
                failures.append(failure)

        if len(candidates) == 1:
            result = base | {"status": "matched", "position": candidates[0]}
            unavailable = [
                failure
                for failure in failures
                if failure["reason"] in {"timeline_missing", "invalid_timeline"}
            ]
            if unavailable:
                result["warnings"] = unavailable
            results.append(result)
        elif candidates:
            track_ids = {candidate["track_id"] for candidate in candidates}
            reason = "ambiguous_tracks" if len(track_ids) > 1 else "ambiguous_segments"
            results.append(
                base | {"status": "ambiguous", "reason": reason, "candidates": candidates}
            )
        else:
            reasons = {failure["reason"] for failure in failures}
            reason = next(iter(reasons)) if len(reasons) == 1 else "no_track_match"
            results.append(base | {"status": "unmatched", "reason": reason, "tracks": failures})
    return results
