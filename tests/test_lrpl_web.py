import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from lrpl.center import CenterClient
from lrpl.index import LocalIndex
from lrpl.metadata import scan_photo_clocks
from lrpl.webapp import (
    AppState,
    format_offset,
    normalized_time,
    page,
    parse_offset,
    thumbnail_bytes,
    valid_origin,
)
from tests.test_lrpl_probe import make_jpeg


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(next(self.responses))


class FakeCenter:
    def __init__(self):
        self.items = None
        self.batch = None

    def report_timelines(self, report_id):
        assert report_id == "report-1"
        return {
            "tracks": [
                {
                    "id": "1aa16638-a09a-4636-9a22-4fc051f60623",
                    "started_at": "2026-07-18T09:00:00Z",
                    "finished_at": "2026-07-18T09:30:00Z",
                }
            ]
        }

    def interpolate_positions(self, report_id, items, *, max_gap_seconds):
        assert report_id == "report-1"
        assert max_gap_seconds == 900
        self.items = items
        return [
            {
                "id": item["id"],
                "status": "matched",
                "position": {
                    "latitude": 50.0,
                    "longitude": 80.0,
                    "method": "interpolated",
                    "track_id": "1aa16638-a09a-4636-9a22-4fc051f60623",
                },
            }
            for item in items
        ]

    def register_photo_batch(self, report_id, client_id, name, calibration, photos):
        self.batch = {
            "report_id": report_id,
            "client_id": client_id,
            "name": name,
            "calibration": calibration,
            "photos": photos,
        }
        return {
            "batch": {"id": "server-batch", "client_id": client_id},
            "photos": [
                {
                    "id": f"server-{photo['client_id']}",
                    "client_id": photo["client_id"],
                    "photo_key": "4c7f1234",
                }
                for photo in photos
            ],
        }


def test_center_client_uses_bearer_and_refuses_remote_plain_http():
    opener = FakeOpener([{"reports": [{"id": "one"}]}])
    client = CenterClient("https://center.example/rpl/", "rpl_identifier_secret", opener=opener)

    assert client.list_reports() == [{"id": "one"}]
    request, timeout = opener.requests[0]
    assert request.full_url == "https://center.example/rpl/api/reports/"
    assert request.get_header("Authorization") == "Bearer rpl_identifier_secret"
    assert timeout == 30
    with pytest.raises(ValueError, match="HTTPS"):
        CenterClient("http://center.example/", "rpl_identifier_secret")


def test_center_client_chunks_large_interpolation_request():
    opener = FakeOpener([{"results": [{"id": "first"}]}, {"results": [{"id": "last"}]}])
    client = CenterClient("http://127.0.0.1:8000/", "rpl_identifier_secret", opener=opener)
    items = [{"id": str(index)} for index in range(10_001)]

    results = client.interpolate_positions("report", items)

    assert results == [{"id": "first"}, {"id": "last"}]
    assert len(opener.requests) == 2
    first_body = json.loads(opener.requests[0][0].data)
    second_body = json.loads(opener.requests[1][0].data)
    assert len(first_body["items"]) == 10_000
    assert len(second_body["items"]) == 1


def test_center_client_chunks_large_photo_batch_request():
    opener = FakeOpener(
        [
            {
                "batch": {"id": "server-batch", "client_id": "local-batch"},
                "photos": [{"client_id": "first"}],
            },
            {
                "batch": {"id": "server-batch", "client_id": "local-batch"},
                "photos": [{"client_id": "last"}],
            },
        ]
    )
    client = CenterClient("http://127.0.0.1:8000/", "rpl_identifier_secret", opener=opener)
    photos = [{"client_id": str(index)} for index in range(10_001)]

    result = client.register_photo_batch("report", "local-batch", "Photos", {}, photos)

    assert result["batch"]["id"] == "server-batch"
    assert result["photos"] == [{"client_id": "first"}, {"client_id": "last"}]
    assert len(opener.requests) == 2
    assert len(json.loads(opener.requests[0][0].data)["photos"]) == 10_000
    assert len(json.loads(opener.requests[1][0].data)["photos"]) == 1


def test_lightweight_clock_scan_reads_exif_without_full_analysis(tmp_path):
    make_jpeg(tmp_path / "photo.jpg", captured_at="2026:07:18 12:05:00")

    photos, failures = scan_photo_clocks(tmp_path)

    assert not failures
    assert len(photos) == 1
    assert photos[0].camera == "Test Camera"
    assert photos[0].captured_at.isoformat() == "2026-07-18T12:05:00"


def test_state_suggests_offset_and_sends_normalized_utc(tmp_path):
    index = LocalIndex(tmp_path / "state" / "lrpl.sqlite3")
    make_jpeg(tmp_path / "photo.jpg", captured_at="2026:07:18 12:05:00")
    indexed, _ = index.scan(tmp_path)
    photo = indexed[0]
    state = AppState(tmp_path, [photo], [], index=index)
    center = FakeCenter()
    state.client = center
    state.reports = [{"id": "report-1", "name": "Trip", "track_count": 1}]

    state.select_report("report-1")

    assert state.calibrations[0].suggested_offset_seconds == -3 * 60 * 60
    counts = state.match({"0": "-03:00"}, 900)
    assert counts == {"matched": 1}
    assert center.items[0]["captured_at"] == "2026-07-18T09:05:00+00:00"
    assert state.results[0]["path"] == "photo.jpg"
    assert state.results[0]["photo_key"] == "4c7f1234"
    assert state.results[0]["logical_day"] == 1
    assert not state.results[0]["day_confirmed"]
    assert center.batch["photos"][0]["time_offset_seconds"] == -10_800
    assert center.batch["photos"][0]["placement"]["source"] == "track"
    rendered = page(state)
    assert f'href="/preview/{photo.id}"' in rendered
    assert "file://" not in rendered
    assert (
        'href="https://nakarte.me/#m=17/50.000000/80.000000&amp;l=O'
        '&amp;q=50.000000%2C%2080.000000"' in rendered
    )

    state.set_logical_day(photo.id, 2)
    assert state.results[0]["logical_day"] == 2
    assert state.results[0]["day_confirmed"]
    assert center.batch["photos"][0]["logical_day"] == 2

    state.match({"0": "-03:00"}, 900)
    assert state.results[0]["logical_day"] == 2
    assert state.confirm_days() == 1

    state._analyze_similarity()
    assert state.similarity_job["status"] == "complete"
    assert state.results[0]["filter_status"] == "selected"
    assert center.batch["photos"][0]["filter_status"] == "selected"
    index.close()


def test_offsets_and_explicit_timezone_normalization():
    assert parse_offset("+03:30") == 12_600
    assert parse_offset("-03:00") == -10_800
    assert format_offset(-10_800) == "-03:00"
    with pytest.raises(ValueError):
        parse_offset("3 hours")
    value = datetime(2026, 7, 18, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    assert normalized_time(value, 0).isoformat() == "2026-07-18T09:00:00+00:00"


def test_origin_check_accepts_loopback_and_opaque_webview():
    assert valid_origin(None, 8123)
    assert valid_origin("null", 8123)
    assert valid_origin("http://127.0.0.1:8123", 8123)
    assert valid_origin("http://localhost:8123", 8123)
    assert not valid_origin("https://example.com", 8123)


def test_page_never_renders_center_token(tmp_path):
    state = AppState(Path(tmp_path), [], [])
    state.client = CenterClient("http://127.0.0.1:8000/", "rpl_identifier_topsecret")
    state.reports = []

    assert "topsecret" not in page(state)


def test_thumbnail_is_small_jpeg(tmp_path):
    path = tmp_path / "photo.jpg"
    make_jpeg(path)

    content = thumbnail_bytes(path, (80, 80))

    assert content.startswith(b"\xff\xd8")
    with Image.open(BytesIO(content)) as image:
        assert image.format == "JPEG"
        assert image.width <= 80
        assert image.height <= 80
