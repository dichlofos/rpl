from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from lrpl.calibration import evaluate_time_offset, suggest_time_offsets
from lrpl.metadata import parse_exif_datetime, read_photo, scan_directory
from lrpl.probe import build_report
from lrpl.similarity import (
    group_similar_photos,
    group_temporal_episodes,
    hamming_distance,
)
from lrpl.timeline import interpolate_position, read_gpx_timeline


def make_jpeg(path, *, captured_at="2026:07:18 12:00:00", shift=0, quality=90):
    image = Image.new("RGB", (160, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((15 + shift, 15, 70 + shift, 85), fill="black")
    draw.line((0, 99, 159, 0), fill="red", width=4)
    exif = Image.Exif()
    exif[271] = "Test"
    exif[272] = "Camera"
    exif[36867] = captured_at
    image.save(path, format="JPEG", quality=quality, exif=exif)


def test_read_photo_extracts_metadata_and_fingerprints(tmp_path):
    path = tmp_path / "photo.jpg"
    make_jpeg(path)

    photo = read_photo(path)

    assert photo.path == path
    assert photo.source_format == "JPEG"
    assert photo.content_sha256
    assert photo.width == 160
    assert photo.height == 100
    assert photo.captured_at is not None
    assert photo.captured_at.isoformat() == "2026-07-18T12:00:00"
    assert not photo.timezone_explicit
    assert photo.camera == "Test Camera"
    assert len(photo.difference_hash) == 64
    assert len(photo.perceptual_hash) == 16
    assert photo.sharpness > 0


def test_scan_directory_reports_broken_jpeg_without_stopping(tmp_path):
    make_jpeg(tmp_path / "good.jpg")
    (tmp_path / "broken.jpeg").write_bytes(b"not a jpeg")
    (tmp_path / "ignored.png").write_bytes(b"not a png")

    photos, failures = scan_directory(tmp_path)

    assert [photo.path.name for photo in photos] == ["good.jpg"]
    assert [failure.path.name for failure in failures] == ["broken.jpeg"]


def test_suggest_time_offset_that_places_photos_inside_track():
    photos = [
        datetime.fromisoformat("2026-07-18T12:05:00"),
        datetime.fromisoformat("2026-07-18T12:40:00"),
    ]
    tracks = [
        (
            datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
        )
    ]

    suggestions = suggest_time_offsets(photos, tracks, tolerance_minutes=0)

    assert suggestions[0].offset_seconds == -3 * 60 * 60
    assert suggestions[0].matched_count == 2


def test_parse_exif_datetime_uses_offset_and_subseconds():
    captured_at, timezone_explicit = parse_exif_datetime("2026:07:18 12:00:00", "+03:00", "347")

    assert timezone_explicit
    assert captured_at.isoformat() == "2026-07-18T12:00:00.347000+03:00"


def test_evaluate_explicit_zero_offset():
    photos = [datetime(2026, 7, 18, 12, 5, tzinfo=timezone.utc)]
    tracks = [
        (
            datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 18, 13, 0, tzinfo=timezone.utc),
        )
    ]

    result = evaluate_time_offset(photos, tracks, 0)

    assert result.matched_count == 1


def test_similarity_groups_recompressed_and_shifted_photos(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    other = tmp_path / "other.jpg"
    make_jpeg(first, quality=95)
    make_jpeg(second, quality=55)
    make_jpeg(other, captured_at="2026:07:18 14:00:00", shift=60)
    photos = [read_photo(path) for path in (first, second, other)]

    assert hamming_distance(photos[0].difference_hash, photos[1].difference_hash) <= 24
    stacks = group_similar_photos(photos)

    assert sorted(len(stack.photos) for stack in stacks) == [1, 2]
    assert stacks[0].kind == "visual"
    assert stacks[0].sharpest in stacks[0].photos


def test_temporal_episodes_use_gaps_between_consecutive_photos(tmp_path):
    paths = [tmp_path / f"photo-{index}.jpg" for index in range(4)]
    for path, captured_at in zip(
        paths,
        (
            "2026:07:18 12:00:00",
            "2026:07:18 12:00:06",
            "2026:07:18 12:00:15",
            "2026:07:18 12:00:30",
        ),
        strict=True,
    ):
        make_jpeg(path, captured_at=captured_at)
    photos = [read_photo(path) for path in paths]

    episodes = group_temporal_episodes(photos, gap_seconds=10)

    assert [len(episode.photos) for episode in episodes] == [3, 1]
    assert episodes[0].duration_seconds == 15


def test_probe_suggests_offsets_per_camera(tmp_path):
    make_jpeg(tmp_path / "first.jpg", captured_at="2026:07:18 12:05:00")
    make_jpeg(tmp_path / "second.jpg", captured_at="2026:07:18 12:40:00")
    track = tmp_path / "track.gpx"
    track.write_text(
        """<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="50" lon="80"><time>2026-07-18T09:00:00Z</time></trkpt>
    <trkpt lat="51" lon="81"><time>2026-07-18T10:00:00Z</time></trkpt>
  </trkseg></trk>
</gpx>
""",
        encoding="utf-8",
    )

    report = build_report(tmp_path, [track])

    calibration = report["camera_calibrations"][0]
    assert calibration["camera"] == "Test Camera"
    assert calibration["offset_suggestions"][0]["offset"] == "-03:00"
    assert report["photos"][0]["source_format"] == "JPEG"
    assert report["analysis"]["perceptual_hash"] == "phash-64-dct-v1"


def test_timeline_preserves_segments_and_interpolates(tmp_path):
    path = tmp_path / "track.gpx"
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="50.0" lon="80.0"><ele>1000</ele><time>2026-07-18T09:00:00Z</time></trkpt>
    <trkpt lat="52.0" lon="84.0"><ele>1200</ele><time>2026-07-18T09:10:00Z</time></trkpt>
  </trkseg></trk>
</gpx>
""",
        encoding="utf-8",
    )

    timeline = read_gpx_timeline(path)
    position = interpolate_position(timeline, datetime(2026, 7, 18, 9, 5, tzinfo=timezone.utc))

    assert len(timeline.segments) == 1
    assert position is not None
    assert position.longitude == pytest.approx(82.0)
    assert position.latitude == pytest.approx(51.0)
    assert position.elevation == pytest.approx(1100)
    assert position.gap_seconds == 600


def test_interpolation_rejects_large_track_gap(tmp_path):
    path = Path(tmp_path) / "track.gpx"
    path.write_text(
        """<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="50" lon="80"><time>2026-07-18T09:00:00Z</time></trkpt>
    <trkpt lat="51" lon="81"><time>2026-07-18T10:00:00Z</time></trkpt>
  </trkseg></trk>
</gpx>
""",
        encoding="utf-8",
    )

    timeline = read_gpx_timeline(path)

    assert interpolate_position(timeline, datetime(2026, 7, 18, 9, 30, tzinfo=timezone.utc)) is None
