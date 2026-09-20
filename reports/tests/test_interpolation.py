import json
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from reports.interpolation import InvalidTimeline, TimelineIndex
from reports.models import TravelReport
from reports.services import set_report_tracks
from tracks.group_services import assign_track
from tracks.models import Track, TrackGroup, TrackTimeline
from tracks.tests.test_tracks import GPX


def payload(*segments):
    return {"version": 1, "time_unit": "ms", "segments": list(segments)}


def point(time_ms, longitude=37.0, latitude=55.0, elevation=100.0):
    return [time_ms, longitude, latitude, elevation]


class TimelineIndexTests(SimpleTestCase):
    def test_exact_point_and_linear_interpolation(self):
        index = TimelineIndex(payload([point(0), point(60_000, longitude=37.002, elevation=120.0)]))

        exact = index.locate(0).positions[0]
        middle = index.locate(30_000).positions[0]

        self.assertEqual(exact.method, "exact")
        self.assertEqual(exact.time_radius_seconds, 0)
        self.assertEqual(middle.method, "interpolated")
        self.assertAlmostEqual(middle.longitude, 37.001)
        self.assertAlmostEqual(middle.elevation, 110.0)
        self.assertEqual(middle.gap_seconds, 60)
        self.assertEqual(middle.time_radius_seconds, 30)
        self.assertGreater(middle.bracket_distance_m, 100)

    def test_interpolation_without_both_elevations_has_no_elevation(self):
        index = TimelineIndex(payload([point(0, elevation=None), point(60_000)]))

        position = index.locate(30_000).positions[0]

        self.assertIsNone(position.elevation)

    def test_longitude_uses_short_path_across_date_line(self):
        index = TimelineIndex(
            payload(
                [
                    point(0, longitude=179.0),
                    point(60_000, longitude=-179.0),
                ]
            )
        )

        position = index.locate(30_000).positions[0]

        self.assertEqual(position.longitude, -180.0)

    def test_failures_distinguish_range_segments_and_large_gap(self):
        index = TimelineIndex(payload([point(10_000)], [point(30_000)]))

        self.assertEqual(index.locate(0).failure.reason, "before_track")
        self.assertEqual(index.locate(40_000).failure.reason, "after_track")
        self.assertEqual(index.locate(20_000).failure.reason, "between_segments")

        gap = TimelineIndex(payload([point(0), point(60_000)])).locate(30_000, max_gap_seconds=10)
        self.assertEqual(gap.failure.reason, "gap_too_large")
        self.assertEqual(gap.failure.details["gap_seconds"], 60)

    def test_overlapping_segments_return_all_candidates(self):
        index = TimelineIndex(
            payload(
                [point(0), point(60_000)],
                [point(0, longitude=38), point(60_000, longitude=39)],
            )
        )

        lookup = index.locate(30_000)

        self.assertEqual(len(lookup.positions), 2)
        self.assertEqual({item.segment_index for item in lookup.positions}, {0, 1})

    def test_invalid_or_empty_timeline_is_explicit(self):
        with self.assertRaises(InvalidTimeline):
            TimelineIndex(payload([point(10_000), point(10_000)]))
        with self.assertRaises(InvalidTimeline):
            TimelineIndex({"version": 2, "time_unit": "ms", "segments": []})
        self.assertEqual(TimelineIndex(payload()).locate(0).failure.reason, "timeline_empty")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class InterpolationApiTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner")
        self.stranger = get_user_model().objects.create_user("stranger")
        self.group = TrackGroup.objects.create(name="Trip", owner=self.owner)
        self.first = self.make_track("Day 1")
        assign_track(self.owner, self.first.pk, self.group.pk)
        self.report = TravelReport.objects.create(
            name="Trip", owner=self.owner, track_group=self.group
        )
        set_report_tracks(self.owner, self.report.pk, [self.first.pk])
        self.url = reverse("reports:interpolate-positions", args=[self.report.public_id])

    def make_track(self, name):
        return Track.objects.create(
            name=name,
            owner=self.owner,
            gpx_file=SimpleUploadedFile(f"{name}.gpx", GPX),
        )

    def post(self, payload_data):
        return self.client.post(
            self.url, data=json.dumps(payload_data), content_type="application/json"
        )

    def test_api_requires_report_access(self):
        self.assertEqual(self.post({"items": []}).status_code, 401)
        self.client.force_login(self.stranger)
        self.assertEqual(self.post({"items": []}).status_code, 403)

    def test_batch_returns_match_and_per_item_validation(self):
        self.client.force_login(self.owner)

        response = self.post(
            {
                "items": [
                    {"id": "photo-1", "captured_at": "2026-08-09T11:02:30+03:00"},
                    {"id": "photo-2", "captured_at": "2026-08-09 08:02:30"},
                    {
                        "id": "photo-3",
                        "captured_at": "2026-08-09T08:02:30Z",
                        "track_id": str(uuid.uuid4()),
                    },
                ]
            }
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["algorithm_version"], 1)
        matched, invalid, unknown = body["results"]
        self.assertEqual(matched["status"], "matched")
        self.assertEqual(matched["position"]["track_id"], str(self.first.public_id))
        self.assertAlmostEqual(matched["position"]["longitude"], 37.615)
        self.assertAlmostEqual(matched["position"]["elevation"], 127.5)
        self.assertEqual(matched["position"]["time_radius_seconds"], 150)
        self.assertEqual((invalid["status"], invalid["reason"]), ("invalid", "invalid_time"))
        self.assertEqual(
            (unknown["status"], unknown["reason"]),
            ("unmatched", "track_not_selected"),
        )

    def test_api_does_not_hide_ambiguous_tracks(self):
        second = self.make_track("Day 2")
        assign_track(self.owner, second.pk, self.group.pk)
        set_report_tracks(self.owner, self.report.pk, [self.first.pk, second.pk])
        self.client.force_login(self.owner)

        response = self.post({"items": [{"id": "photo", "captured_at": "2026-08-09T08:02:30Z"}]})

        result = response.json()["results"][0]
        self.assertEqual((result["status"], result["reason"]), ("ambiguous", "ambiguous_tracks"))
        self.assertEqual(len(result["candidates"]), 2)

    def test_api_reports_gap_missing_timeline_and_outside_range(self):
        self.client.force_login(self.owner)
        too_wide = self.post(
            {
                "max_gap_seconds": 60,
                "items": [{"id": "gap", "captured_at": "2026-08-09T08:02:30Z"}],
            }
        ).json()["results"][0]
        before = self.post(
            {"items": [{"id": "before", "captured_at": "2026-08-09T07:59:00Z"}]}
        ).json()["results"][0]
        TrackTimeline.objects.filter(track=self.first).delete()
        missing = self.post(
            {"items": [{"id": "missing", "captured_at": "2026-08-09T08:02:30Z"}]}
        ).json()["results"][0]

        self.assertEqual(too_wide["reason"], "gap_too_large")
        self.assertEqual(too_wide["tracks"][0]["gap_seconds"], 300)
        self.assertEqual(before["reason"], "before_track")
        self.assertEqual(missing["reason"], "timeline_missing")

    def test_api_rejects_invalid_request_shape(self):
        self.client.force_login(self.owner)

        duplicate = self.post(
            {
                "items": [
                    {"id": "same", "captured_at": "2026-08-09T08:00:00Z"},
                    {"id": "same", "captured_at": "2026-08-09T08:01:00Z"},
                ]
            }
        )
        invalid_gap = self.post({"max_gap_seconds": 0, "items": []})

        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(invalid_gap.status_code, 400)
