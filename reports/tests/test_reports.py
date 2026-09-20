import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import TravelReport
from reports.services import set_report_tracks
from tracks.group_services import assign_track, delete_group
from tracks.models import Track, TrackGroup, TrackTimeline
from tracks.tests.test_tracks import GPX
from tracks.tests.test_waypoints import ONLY_WPT


class ReportTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = get_user_model().objects.create_user("owner")
        self.stranger = get_user_model().objects.create_user("stranger")
        self.group = TrackGroup.objects.create(name="All tracks", owner=self.owner)
        self.other_group = TrackGroup.objects.create(name="Other", owner=self.owner)
        self.first = self.make_track("Day 1")
        self.second = self.make_track("Day 2")
        self.unselected = self.make_track("Extra reconnaissance")
        self.foreign = self.make_track("Other route")
        for track in (self.first, self.second, self.unselected):
            assign_track(self.owner, track.pk, self.group.pk)
        assign_track(self.owner, self.foreign.pk, self.other_group.pk)
        self.report = TravelReport.objects.create(
            name="High Alay", owner=self.owner, track_group=self.group
        )

    def make_track(self, name):
        return Track.objects.create(
            name=name,
            owner=self.owner,
            gpx_file=SimpleUploadedFile(f"{name}.gpx", GPX),
        )

    def test_ordered_selection_can_use_subset_of_group(self):
        set_report_tracks(self.owner, self.report.pk, [self.second.pk, self.first.pk])

        self.assertEqual(
            list(self.report.track_links.values_list("track_id", "position")),
            [(self.second.pk, 0), (self.first.pk, 1)],
        )
        self.assertNotIn(self.unselected, self.report.tracks.all())

    def test_selection_rejects_duplicates_foreign_group_and_foreign_user(self):
        with self.assertRaises(ValidationError):
            set_report_tracks(self.owner, self.report.pk, [self.first.pk, self.first.pk])
        with self.assertRaises(ValidationError):
            set_report_tracks(self.owner, self.report.pk, [self.foreign.pk])
        with self.assertRaises(PermissionDenied):
            set_report_tracks(self.stranger, self.report.pk, [self.first.pk])
        self.assertFalse(self.report.track_links.exists())

    def test_deleting_source_group_preserves_report_selection(self):
        set_report_tracks(self.owner, self.report.pk, [self.first.pk, self.second.pk])

        delete_group(self.owner, self.group.pk)

        self.report.refresh_from_db()
        self.assertIsNone(self.report.track_group)
        self.assertEqual(
            list(self.report.track_links.values_list("track_id", flat=True)),
            [self.first.pk, self.second.pk],
        )
        set_report_tracks(self.owner, self.report.pk, [self.second.pk, self.first.pk])
        self.assertEqual(
            list(self.report.track_links.values_list("track_id", flat=True)),
            [self.second.pk, self.first.pk],
        )
        with self.assertRaises(ValidationError):
            set_report_tracks(
                self.owner,
                self.report.pk,
                [self.second.pk, self.first.pk, self.unselected.pk],
            )

    def test_report_requires_source_group_on_creation_and_matching_owner(self):
        with self.assertRaises(ValidationError):
            TravelReport.objects.create(name="No group", owner=self.owner)
        with self.assertRaises(ValidationError):
            TravelReport.objects.create(
                name="Wrong owner", owner=self.stranger, track_group=self.group
            )

    def test_timeline_api_is_private_ordered_and_omits_extra_group_tracks(self):
        set_report_tracks(self.owner, self.report.pk, [self.second.pk, self.first.pk])
        url = reverse("reports:track-timelines", args=[self.report.public_id])

        self.assertEqual(self.client.get(url).status_code, 401)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.owner)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["report"]["id"], str(self.report.public_id))
        self.assertEqual(
            [item["id"] for item in payload["tracks"]],
            [str(self.second.public_id), str(self.first.public_id)],
        )
        self.assertEqual([item["position"] for item in payload["tracks"]], [0, 1])
        self.assertTrue(all(item["timeline_status"] == "ready" for item in payload["tracks"]))
        self.assertNotIn(str(self.unselected.public_id), response.content.decode())

    def test_timeline_api_reports_missing_derived_data(self):
        set_report_tracks(self.owner, self.report.pk, [self.first.pk])
        TrackTimeline.objects.filter(track=self.first).delete()
        self.client.force_login(self.owner)

        response = self.client.get(reverse("reports:track-timelines", args=[self.report.public_id]))

        item = response.json()["tracks"][0]
        self.assertEqual(item["timeline_status"], "missing")
        self.assertIsNone(item["timed_points_count"])
        self.assertIsNone(item["timeline"])

    def test_timeline_api_distinguishes_track_without_timed_points(self):
        empty = Track.objects.create(
            name="Waypoints only",
            owner=self.owner,
            gpx_file=SimpleUploadedFile("waypoints.gpx", ONLY_WPT),
        )
        assign_track(self.owner, empty.pk, self.group.pk)
        set_report_tracks(self.owner, self.report.pk, [empty.pk])
        self.client.force_login(self.owner)

        response = self.client.get(reverse("reports:track-timelines", args=[self.report.public_id]))

        item = response.json()["tracks"][0]
        self.assertEqual(item["timeline_status"], "empty")
        self.assertEqual(item["timed_points_count"], 0)
        self.assertEqual(item["timeline"]["segments"], [])
