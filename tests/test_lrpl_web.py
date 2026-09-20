import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lrpl.center import CenterClient
from lrpl.metadata import PhotoClockMetadata, scan_photo_clocks
from lrpl.webapp import AppState, format_offset, normalized_time, page, parse_offset
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

    def report_timelines(self, report_id):
        assert report_id == "report-1"
        return {
            "tracks": [
                {
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
                },
            }
            for item in items
        ]


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


def test_lightweight_clock_scan_reads_exif_without_full_analysis(tmp_path):
    make_jpeg(tmp_path / "photo.jpg", captured_at="2026:07:18 12:05:00")

    photos, failures = scan_photo_clocks(tmp_path)

    assert not failures
    assert len(photos) == 1
    assert photos[0].camera == "Test Camera"
    assert photos[0].captured_at.isoformat() == "2026-07-18T12:05:00"


def test_state_suggests_offset_and_sends_normalized_utc(tmp_path):
    photo = PhotoClockMetadata(
        path=tmp_path / "photo.jpg",
        captured_at=datetime(2026, 7, 18, 12, 5),  # noqa: DTZ001 - camera wall time
        timezone_explicit=False,
        camera="Camera",
    )
    state = AppState(tmp_path, [photo], [])
    center = FakeCenter()
    state.client = center
    state.reports = [{"id": "report-1", "name": "Trip", "track_count": 1}]

    state.select_report("report-1")

    assert state.calibrations[0].suggested_offset_seconds == -3 * 60 * 60
    counts = state.match({"0": "-03:00"}, 900)
    assert counts == {"matched": 1}
    assert center.items[0]["captured_at"] == "2026-07-18T09:05:00+00:00"
    assert state.results[0]["path"] == "photo.jpg"


def test_offsets_and_explicit_timezone_normalization():
    assert parse_offset("+03:30") == 12_600
    assert parse_offset("-03:00") == -10_800
    assert format_offset(-10_800) == "-03:00"
    with pytest.raises(ValueError):
        parse_offset("3 hours")
    value = datetime(2026, 7, 18, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    assert normalized_time(value, 0).isoformat() == "2026-07-18T09:00:00+00:00"


def test_page_never_renders_center_token(tmp_path):
    state = AppState(Path(tmp_path), [], [])
    state.client = CenterClient("http://127.0.0.1:8000/", "rpl_identifier_topsecret")
    state.reports = []

    assert "topsecret" not in page(state)
