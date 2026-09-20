import argparse
import json
from collections import Counter
from pathlib import Path

from .calibration import suggest_time_offsets
from .metadata import scan_directory
from .similarity import group_similar_photos
from .timeline import read_gpx_timeline


def _format_offset(seconds):
    sign = "+" if seconds >= 0 else "-"
    minutes = abs(seconds) // 60
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def build_report(directory, gpx_paths=(), *, window_seconds=60, hash_distance=24):
    photos, failures = scan_directory(directory)
    timelines = [read_gpx_timeline(path) for path in gpx_paths]
    track_intervals = [(item.started_at, item.finished_at) for item in timelines]
    cameras = sorted({photo.camera for photo in photos})
    camera_calibrations = []
    for camera in cameras:
        camera_photos = [photo for photo in photos if photo.camera == camera]
        times = [photo.captured_at for photo in camera_photos if photo.captured_at]
        suggestions = suggest_time_offsets(times, track_intervals)
        camera_calibrations.append(
            {
                "camera": camera,
                "photo_count": len(camera_photos),
                "photos_with_time": len(times),
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
    stacks = group_similar_photos(
        photos, window_seconds=window_seconds, hash_distance=hash_distance
    )
    return {
        "directory": str(Path(directory).resolve()),
        "photo_count": len(photos),
        "failure_count": len(failures),
        "cameras": dict(sorted(Counter(photo.camera for photo in photos).items())),
        "photos_with_time": sum(photo.captured_at is not None for photo in photos),
        "photos_with_gps": sum(photo.latitude is not None for photo in photos),
        "camera_calibrations": camera_calibrations,
        "similarity_stacks": [
            {
                "size": len(stack.photos),
                "sharpest": str(stack.sharpest.path),
                "photos": [str(photo.path) for photo in stack.photos],
            }
            for stack in stacks
            if len(stack.photos) > 1
        ],
        "failures": [failure.as_json() for failure in failures],
    }


def create_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Исследовательский анализ каталога "
            "фотографий для RPL."
        )
    )
    parser.add_argument(
        "directory", type=Path, help="Каталог с JPEG-фотографиями."
    )
    parser.add_argument(
        "--gpx",
        type=Path,
        action="append",
        default=[],
        help=(
            "GPX для временной калибровки; "
            "параметр можно повторять."
        ),
    )
    parser.add_argument("--window-seconds", type=int, default=60)
    parser.add_argument("--hash-distance", type=int, default=24)
    parser.add_argument("--json", action="store_true", help="Вывести полный JSON.")
    return parser


def main(argv=None):
    args = create_parser().parse_args(argv)
    report = build_report(
        args.directory,
        args.gpx,
        window_seconds=args.window_seconds,
        hash_distance=args.hash_distance,
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
        for item in calibration["offset_suggestions"]:
            print(f"  {item['offset']}: {item['matched_count']}/{item['photo_count']}")
    print(f"Стопок с похожими кадрами: {len(report['similarity_stacks'])}")
    for failure in report["failures"]:
        print(f"Ошибка: {failure['path']}: {failure['error']}")
    return 0 if not report["failures"] else 2
