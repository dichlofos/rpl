import json
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from tracks.models import Track

GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="RPL tests" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Test route</name><trkseg>
    <trkpt lat="55.7500" lon="37.6100"><ele>120</ele><time>2026-08-09T08:00:00Z</time></trkpt>
    <trkpt lat="55.7510" lon="37.6200"><ele>135</ele><time>2026-08-09T08:05:00Z</time></trkpt>
    <trkpt lat="55.7520" lon="37.6300"><ele>125</ele><time>2026-08-09T08:10:00Z</time></trkpt>
  </trkseg></trk>
</gpx>"""


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TrackTests(TestCase):
    def make_track(self, name=""):
        return Track.objects.create(
            name=name,
            gpx_file=SimpleUploadedFile("route.gpx", GPX, content_type="application/gpx+xml"),
        )

    def test_import_extracts_geometry_and_metadata(self):
        track = self.make_track()

        self.assertEqual(track.name, "Test route")
        self.assertIsInstance(track.public_id, uuid.UUID)
        self.assertEqual(track.points_count, 3)
        self.assertEqual(track.geojson["geometry"]["type"], "LineString")
        self.assertEqual(track.duration_s, 600)
        self.assertGreater(track.distance_m, 1000)
        self.assertEqual(track.min_elevation_m, 120)
        self.assertEqual(track.max_elevation_m, 135)
        self.assertRegex(
            track.gpx_file.name,
            r"^tracks/[0-9a-f]{3}/[0-9a-f]{3}/[0-9a-f]{32}\.gpx$",
        )
        self.assertNotIn("route", track.gpx_file.name)

    def test_invalid_gpx_is_rejected(self):
        track = Track(gpx_file=SimpleUploadedFile("broken.gpx", b"not xml"))

        with self.assertRaises(ValidationError):
            track.save()

    def test_doctype_is_rejected(self):
        unsafe = b'<!DOCTYPE gpx [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><gpx>&xxe;</gpx>'
        track = Track(gpx_file=SimpleUploadedFile("unsafe.gpx", unsafe))

        with self.assertRaises(ValidationError):
            track.save()

    def test_list_detail_and_geojson(self):
        track = self.make_track()

        list_response = self.client.get(reverse("tracks:list"))
        detail_response = self.client.get(track.get_absolute_url())
        geojson_response = self.client.get(reverse("tracks:geojson", args=[track.public_id]))

        self.assertContains(list_response, "Test route")
        self.assertContains(list_response, 'data-track-view="cards"')
        self.assertContains(list_response, 'data-track-view="table"')
        self.assertContains(list_response, "Дистанция")
        self.assertContains(detail_response, "track-map")
        self.assertEqual(geojson_response.status_code, 200)
        self.assertEqual(geojson_response.json()["properties"]["name"], "Test route")

    def test_track_can_be_downloaded_as_gpx(self):
        track = self.make_track("Поход выходного дня")

        detail_response = self.client.get(track.get_absolute_url())
        response = self.client.get(reverse("tracks:download", args=[track.public_id]))

        self.assertContains(detail_response, "Скачать GPX")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/gpx+xml")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn(".gpx", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), GPX)

    def test_owner_can_move_and_trim_track_points(self):
        owner = get_user_model().objects.create_user("owner", password="password")
        track = self.make_track()
        track.owner = owner
        track.save(update_fields=["owner"])
        old_file_name = track.gpx_file.name
        self.client.force_login(owner)

        response = self.client.post(
            reverse("tracks:edit", args=[track.public_id]),
            data=json.dumps(
                {
                    "start_index": 1,
                    "end_index": 3,
                    "coordinates": [[37.621, 55.7515], [37.63, 55.752]],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        track.refresh_from_db()
        self.assertEqual(track.points_count, 2)
        self.assertNotEqual(track.gpx_file.name, old_file_name)
        self.assertEqual(track.geojson["geometry"]["coordinates"][0][:2], [37.621, 55.7515])
        self.assertEqual(track.min_elevation_m, 125)
        self.assertEqual(track.max_elevation_m, 135)
        self.assertEqual(track.started_at.isoformat(), "2026-08-09T08:05:00+00:00")
        self.assertEqual(track.duration_s, 300)
        self.assertEqual(response.json()["stats"]["points_count"], 2)

    def test_owner_can_delete_non_contiguous_track_points(self):
        owner = get_user_model().objects.create_user("owner", password="password")
        track = self.make_track()
        track.owner = owner
        track.save(update_fields=["owner"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("tracks:edit", args=[track.public_id]),
            data=json.dumps(
                {
                    "retained_indices": [0, 2],
                    "coordinates": [[37.61, 55.75], [37.63, 55.752]],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        track.refresh_from_db()
        self.assertEqual(track.points_count, 2)
        self.assertEqual(track.geojson["geometry"]["coordinates"], [
            [37.61, 55.75, 120.0],
            [37.63, 55.752, 125.0],
        ])
        self.assertEqual(track.duration_s, 600)

    def test_track_edit_rejects_unsorted_retained_indices(self):
        owner = get_user_model().objects.create_user("owner", password="password")
        track = self.make_track()
        track.owner = owner
        track.save(update_fields=["owner"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("tracks:edit", args=[track.public_id]),
            data=json.dumps(
                {
                    "retained_indices": [2, 0],
                    "coordinates": [[37.63, 55.752], [37.61, 55.75]],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Track.objects.get(pk=track.pk).points_count, 3)

    def test_non_owner_cannot_edit_track(self):
        owner = get_user_model().objects.create_user("owner", password="password")
        stranger = get_user_model().objects.create_user("stranger", password="password")
        track = self.make_track()
        track.owner = owner
        track.save(update_fields=["owner"])
        self.client.force_login(stranger)

        response = self.client.post(
            reverse("tracks:edit", args=[track.public_id]),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_track_edit_validates_coordinates(self):
        owner = get_user_model().objects.create_user("owner", password="password")
        track = self.make_track()
        track.owner = owner
        track.save(update_fields=["owner"])
        self.client.force_login(owner)

        response = self.client.post(
            reverse("tracks:edit", args=[track.public_id]),
            data=json.dumps(
                {
                    "start_index": 0,
                    "end_index": 3,
                    "coordinates": [[37.61, 95], [37.62, 55.751], [37.63, 55.752]],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Track.objects.get(pk=track.pk).points_count, 3)

    def test_edit_controls_are_only_visible_to_owner(self):
        owner = get_user_model().objects.create_user("owner", password="password")
        track = self.make_track()
        track.owner = owner
        track.save(update_fields=["owner"])

        anonymous_response = self.client.get(track.get_absolute_url())
        self.client.force_login(owner)
        owner_response = self.client.get(track.get_absolute_url())

        self.assertNotContains(anonymous_response, "track-editor")
        self.assertContains(owner_response, "track-editor")
        self.assertContains(owner_response, "Сначала выберите точку на треке", count=2)

    def test_duplicate_names_get_unique_public_ids(self):
        first = self.make_track("Один маршрут")
        second = self.make_track("Один маршрут")

        self.assertNotEqual(first.public_id, second.public_id)

    def test_admin_reports_invalid_gpx_as_form_error(self):
        user = get_user_model().objects.create_superuser("admin", "admin@example.test", "password")
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin:tracks_track_add"),
            {
                "name": "Broken",
                "description": "",
                "owner": "",
                "gpx_file": SimpleUploadedFile("broken.gpx", b"not xml"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Не удалось разобрать GPX-файл")
        self.assertEqual(Track.objects.count(), 0)

    def test_upload_requires_login(self):
        response = self.client.get(reverse("tracks:upload"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('tracks:upload')}")

    def test_authenticated_user_can_upload_track(self):
        user = get_user_model().objects.create_user("rider", password="password")
        self.client.force_login(user)

        response = self.client.post(
            reverse("tracks:upload"),
            {
                "name": "Поход выходного дня",
                "description": "Тестовое описание",
                "gpx_file": SimpleUploadedFile(
                    "weekend.gpx", GPX, content_type="application/gpx+xml"
                ),
            },
        )

        track = Track.objects.get()
        self.assertRedirects(response, track.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(track.owner, user)
        self.assertEqual(track.name, "Поход выходного дня")

    def test_upload_page_contains_only_public_fields(self):
        user = get_user_model().objects.create_user("rider", password="password")
        self.client.force_login(user)

        response = self.client.get(reverse("tracks:upload"))

        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="description"')
        self.assertContains(response, 'name="gpx_file"')
        self.assertNotContains(response, 'name="owner"')
        self.assertNotContains(response, 'name="public_id"')
