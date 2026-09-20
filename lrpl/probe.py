import argparse
import json
from collections import Counter
from pathlib import Path

from .calibration import evaluate_time_offset, suggest_time_offsets
from .metadata import (
    DIFFERENCE_HASH_VERSION,
    PERCEPTUAL_HASH_VERSION,
    SHARPNESS_VERSION,
    scan_directory,
)
from .similarity import group_similar_photos, group_temporal_episodes
from .timeline import read_gpx_timeline


def _format_offset(seconds):
    sign = "+" if seconds >= 0 else "-"
    minutes = abs(seconds) // 60
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def build_report(
    directory,
    gpx_paths=(),
    *,
    episode_gap_seconds=10,
    difference_hash_distance=24,
    perceptual_hash_distance=10,
):
    photos, failures = scan_directory(directory)
    timelines = [read_gpx_timeline(path) for path in gpx_paths]
    track_intervals = [(item.started_at, item.finished_at) for item in timelines]
    cameras = sorted({photo.camera for photo in photos})
    camera_calibrations = []
    for camera in cameras:
        camera_photos = [photo for photo in photos if photo.camera == camera]
        times = [photo.captured_at for photo in camera_photos if photo.captured_at]
        explicit_timezone_count = sum(
            photo.timezone_explicit for photo in camera_photos if photo.captured_at
        )
        suggestions = suggest_time_offsets(times, track_intervals)
        if times and explicit_timezone_count == len(times):
            zero = evaluate_time_offset(times, track_intervals, 0)
            suggestions = [zero] + [item for item in suggestions if item.offset_seconds != 0]
        camera_calibrations.append(
            {
                "camera": camera,
                "photo_count": len(camera_photos),
                "photos_with_time": len(times),
                "photos_with_explicit_timezone": explicit_timezone_count,
                "recommendation_basis": (
                    "exif-timezone"
                    if times and explicit_timezone_count == len(times)
                    else "track-overlap"
                ),
                "captured_from": min(times).isoformat() if times else None,
                "captured_to": max(times).isoformat() if times else None,
                "offset_suggestions": [
                    {
                        "offset": _format_offset(item.offset_seconds),
                        "offset_seconds": item.offset_seconds,
                        "matched_count": item.matched_count,
                        "photo_count": item.photo_count,
                    }
                    for item in suggestions
                ],
            }
        )
    episodes = group_temporal_episodes(photos, gap_seconds=episode_gap_seconds)
    episode_reports = []
    for episode in episodes:
        if len(episode.photos) < 2:
            continue
        stacks = group_similar_photos(
            episode.photos,
            window_seconds=max(episode.duration_seconds, episode_gap_seconds),
            difference_hash_distance=difference_hash_distance,
            perceptual_hash_distance=perceptual_hash_distance,
        )
        episode_reports.append(
            {
                "camera": episode.photos[0].camera,
                "size": len(episode.photos),
                "started_at": episode.started_at.isoformat(),
                "finished_at": episode.finished_at.isoformat(),
                "duration_seconds": episode.duration_seconds,
                "photos": [str(photo.path) for photo in episode.photos],
                "visual_stacks": [
                    {
                        "kind": stack.kind,
                        "size": len(stack.photos),
                        "sharpest": str(stack.sharpest.path),
                        "photos": [str(photo.path) for photo in stack.photos],
                    }
                    for stack in stacks
                    if len(stack.photos) > 1
                ],
            }
        )
    return {
        "directory": str(Path(directory).resolve()),
        "analysis": {
            "episode_gap_seconds": episode_gap_seconds,
            "difference_hash": DIFFERENCE_HASH_VERSION,
            "difference_hash_distance": difference_hash_distance,
            "perceptual_hash": PERCEPTUAL_HASH_VERSION,
            "perceptual_hash_distance": perceptual_hash_distance,
            "sharpness": SHARPNESS_VERSION,
        },
        "photo_count": len(photos),
        "failure_count": len(failures),
        "cameras": dict(sorted(Counter(photo.camera for photo in photos).items())),
        "photos_with_time": sum(photo.captured_at is not None for photo in photos),
        "photos_with_gps": sum(photo.latitude is not None for photo in photos),
        "photos": [photo.as_json() for photo in photos],
        "camera_calibrations": camera_calibrations,
        "temporal_episodes": episode_reports,
        "failures": [failure.as_json() for failure in failures],
    }


def create_parser():
    parser = argparse.ArgumentParser(
        description=("Исследовательский анализ каталога фотографий для RPL.")
    )
    parser.add_argument("directory", type=Path, help="Каталог с JPEG-фотографиями.")
    parser.add_argument(
        "--gpx",
        type=Path,
        action="append",
        default=[],
        help=("GPX для временной калибровки; параметр можно повторять."),
    )
    parser.add_argument("--episode-gap-seconds", type=int, default=10)
    parser.add_argument("--difference-hash-distance", type=int, default=24)
    parser.add_argument("--perceptual-hash-distance", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="Вывести полный JSON.")
    return parser


def main(argv=None):
    args = create_parser().parse_args(argv)
    report = build_report(
        args.directory,
        args.gpx,
        episode_gap_seconds=args.episode_gap_seconds,
        difference_hash_distance=args.difference_hash_distance,
        perceptual_hash_distance=args.perceptual_hash_distance,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"Фотографий: {report['photo_count']}")
    print(f"Ошибок чтения: {report['failure_count']}")
    print(f"С временем: {report['photos_with_time']}")
    print(f"С GPS: {report['photos_with_gps']}")
    print("Камеры:")
    for camera, count in report["cameras"].items():
        print(f"  {camera}: {count}")
    for calibration in report["camera_calibrations"]:
        if not calibration["offset_suggestions"]:
            continue
        print(
            "Предлагаемые поправки для "
            f"{calibration['camera']} "
            "(нормализованное = исходное + поправка):"
        )
        if calibration["recommendation_basis"] == "exif-timezone":
            print("  Часовой пояс задан в EXIF; базовая поправка равна +00:00.")
        for item in calibration["offset_suggestions"]:
            print(f"  {item['offset']}: {item['matched_count']}/{item['photo_count']}")
    visual_stacks = sum(len(episode["visual_stacks"]) for episode in report["temporal_episodes"])
    print(f"Временных эпизодов: {len(report['temporal_episodes'])}")
    print(f"Визуальных стопок внутри эпизодов: {visual_stacks}")
    for failure in report["failures"]:
        print(f"Ошибка: {failure['path']}: {failure['error']}")
    return 0 if not report["failures"] else 2
