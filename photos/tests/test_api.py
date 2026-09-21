import json
import re
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from photos.models import PhotoAsset, PhotoBatch, PhotoPlacement
from reports.models import PersonalApiToken, TravelReport
from reports.services import set_report_tracks
from tracks.group_services import assign_track
from tracks.models import Track, TrackGroup
from tracks.tests.test_tracks import GPX


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PhotoBatchApiTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner")
        self.stranger = get_user_model().objects.create_user("stranger")
        self.group = TrackGroup.objects.create(name="Trip", owner=self.owner)
        self.track = Track.objects.create(
            name="Day 1",
            owner=self.owner,
            gpx_file=SimpleUploadedFile("day.gpx", GPX),
        )
        assign_track(self.owner, self.track.pk, self.group.pk)
        self.report = TravelReport.objects.create(
            name="Trip", owner=self.owner, track_group=self.group
        )
        set_report_tracks(self.owner, self.report.pk, [self.track.pk])
        _, self.token = PersonalApiToken.issue(self.owner, "Laptop")
        _, self.foreign_token = PersonalApiToken.issue(self.stranger, "Laptop")
        self.url = reverse("photos:register-batch", args=[self.report.public_id])
        self.batch_id = str(uuid.uuid4())
        self.photo_id = str(uuid.uuid4())

    @staticmethod
    def photo(client_id, **changes):
        result = {
            "client_id": client_id,
            "relative_path": "day-01/photo.jpg",
            "file_size": 123_456,
            "file_mtime_ns": 1_725_000_000_000_000_000,
            "captured_at_raw": "2026-07-18T12:05:00",
            "timezone_explicit": False,
            "time_offset_seconds": -10_800,
            "captured_at_normalized": "2026-07-18T09:05:00+00:00",
            "logical_day": 1,
            "day_confirmed": False,
            "camera": "Test Camera",
        }
        result.update(changes)
        return result

    def payload(self, photos):
        return {
            "client_id": self.batch_id,
            "name": "fedya",
            "calibration": {"Test Camera": -10_800},
            "photos": photos,
        }

    def post(self, payload, token=None):
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def test_requires_bearer_token_and_report_access(self):
        payload = self.payload([])

        self.assertEqual(self.post(payload).status_code, 401)
        self.client.force_login(self.owner)
        self.assertEqual(self.post(payload).status_code, 401)
        self.assertEqual(self.post(payload, self.foreign_token).status_code, 403)

    def test_registers_metadata_and_track_placement_idempotently(self):
        placement = {
            "source": "track",
            "longitude": 37.615,
            "latitude": 55.752,
            "elevation": 127.5,
            "track_id": str(self.track.public_id),
            "algorithm_version": 1,
            "details": {"method": "interpolated", "gap_seconds": 300},
            "confirmed": False,
        }
        response = self.post(
            self.payload(
                [
                    self.photo(
                        self.photo_id,
                        placement=placement,
                        logical_day=3,
                        day_confirmed=True,
                    )
                ]
            ),
            self.token,
        )

        self.assertEqual(response.status_code, 200)
        first = response.json()
        self.assertEqual(PhotoBatch.objects.count(), 1)
        self.assertEqual(PhotoAsset.objects.count(), 1)
        self.assertEqual(PhotoPlacement.objects.count(), 1)
        self.assertRegex(first["photos"][0]["photo_key"], re.compile(r"^[0-9a-f]{8}$"))
        asset = PhotoAsset.objects.get()
        self.assertEqual(asset.logical_day, 3)
        self.assertTrue(asset.day_confirmed)
        self.assertEqual(asset.placement.track, self.track)
        self.assertAlmostEqual(asset.placement.point.x, 37.615)

        updated_payload = self.payload(
            [self.photo(self.photo_id, relative_path="day-01/renamed.jpg")]
        )
        repeated = self.post(updated_payload, self.token)

        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(PhotoBatch.objects.count(), 1)
        self.assertEqual(PhotoAsset.objects.count(), 1)
        self.assertEqual(PhotoPlacement.objects.count(), 0)
        asset.refresh_from_db()
        self.assertEqual(asset.relative_path, "day-01/renamed.jpg")
        self.assertEqual(repeated.json()["photos"][0]["photo_key"], first["photos"][0]["photo_key"])

    def test_registers_exif_placement_without_track(self):
        placement = {
            "source": "exif",
            "longitude": 72.123,
            "latitude": 39.456,
            "elevation": None,
            "details": {},
            "confirmed": False,
        }

        response = self.post(
            self.payload([self.photo(self.photo_id, placement=placement)]), self.token
        )

        self.assertEqual(response.status_code, 200)
        stored = PhotoPlacement.objects.get()
        self.assertEqual(stored.source, "exif")
        self.assertIsNone(stored.track)

    def test_rejects_unselected_track_and_unsafe_relative_path(self):
        foreign_track = Track.objects.create(
            name="Other",
            owner=self.owner,
            gpx_file=SimpleUploadedFile("other.gpx", GPX),
        )
        invalid_placement = {
            "source": "track",
            "longitude": 37,
            "latitude": 55,
            "track_id": str(foreign_track.public_id),
            "confirmed": False,
        }

        bad_track = self.post(
            self.payload([self.photo(self.photo_id, placement=invalid_placement)]), self.token
        )
        bad_path = self.post(
            self.payload([self.photo(self.photo_id, relative_path="../photo.jpg")]), self.token
        )

        self.assertEqual(bad_track.status_code, 400)
        self.assertEqual(bad_path.status_code, 400)
        self.assertFalse(PhotoAsset.objects.exists())

    def test_rejects_confirmed_photo_without_day(self):
        response = self.post(
            self.payload([self.photo(self.photo_id, logical_day=None, day_confirmed=True)]),
            self.token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(PhotoAsset.objects.exists())

    def test_local_batch_cannot_move_to_another_report(self):
        first = self.post(self.payload([self.photo(self.photo_id)]), self.token)
        other = TravelReport.objects.create(name="Other", owner=self.owner, track_group=self.group)
        other_url = reverse("photos:register-batch", args=[other.public_id])

        response = self.client.post(
            other_url,
            data=json.dumps(self.payload([])),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(PhotoBatch.objects.count(), 1)
