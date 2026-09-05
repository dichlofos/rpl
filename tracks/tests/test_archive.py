import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from tracks.archive_services import restore_original
from tracks.group_services import assign_track
from tracks.models import Track, TrackGroup
from tracks.services import edit_gpx
from tracks.tests.test_tracks import GPX


class ArchiveTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = get_user_model().objects.create_user("owner")
        self.track = Track.objects.create(
            name="My track",
            description="Trip",
            owner=self.owner,
            gpx_file=SimpleUploadedFile("raw.gpx", GPX),
        )

    def read(self, field):
        with field.open("rb") as stream:
            return stream.read()

    def edit(self):
        self.track.gpx_file = ContentFile(
            edit_gpx(
                GPX,
                [[37.61, 55.75], [37.63, 55.752]],
                retained_indices=[0, 2],
            ),
            name="edited.gpx",
        )
        self.track.save()

    def test_upload_keeps_two_independent_exact_copies(self):
        self.assertNotEqual(self.track.gpx_file.name, self.track.original_gpx_file.name)
        self.assertEqual(self.read(self.track.original_gpx_file), GPX)
        self.assertEqual(self.read(self.track.gpx_file), GPX)

    def test_edit_and_restore_preserve_archive_metadata_and_membership(self):
        group = TrackGroup.objects.create(name="Trip", owner=self.owner)
        assign_track(self.owner, self.track.pk, group.pk)
        original_name = self.track.original_gpx_file.name
        old_name = self.track.gpx_file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.edit()
        self.assertFalse(self.track.gpx_file.storage.exists(old_name))
        self.assertEqual(self.read(self.track.original_gpx_file), GPX)
        self.assertEqual(self.track.points_count, 2)
        with patch("regions.services.classify_track") as classify:
            restore_original(self.owner, self.track.pk)
            classify.assert_called_once()
        self.track.refresh_from_db()
        self.assertEqual(self.read(self.track.gpx_file), GPX)
        self.assertEqual(self.track.original_gpx_file.name, original_name)
        self.assertEqual(self.track.points_count, 3)
        self.assertEqual(self.track.max_elevation_m, 135)
        self.assertEqual(self.track.duration_s, 600)
        self.assertEqual((self.track.name, self.track.description), ("My track", "Trip"))
        self.assertEqual(list(self.track.groups.all()), [group])

    def test_archive_cannot_be_replaced_or_cleared(self):
        for replacement in ["", ContentFile(GPX, name="replacement.gpx")]:
            self.track.refresh_from_db()
            self.track.original_gpx_file = replacement
            with self.assertRaises(ValidationError):
                self.track.save()
        self.track.refresh_from_db()
        self.assertEqual(self.read(self.track.original_gpx_file), GPX)

    def test_rollback_does_not_delete_previous_working_file(self):
        old_name = self.track.gpx_file.name
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.edit()
            raise RuntimeError("rollback")
        self.track.refresh_from_db()
        self.assertEqual(self.track.gpx_file.name, old_name)
        self.assertEqual(self.read(self.track.gpx_file), GPX)
        self.assertEqual(self.read(self.track.original_gpx_file), GPX)

    def test_classification_failure_rolls_back_database_and_preserves_old_file(self):
        with (
            patch("regions.services.classify_track", side_effect=RuntimeError("failure")),
            self.assertRaises(RuntimeError),
        ):
            self.edit()
        self.track.refresh_from_db()
        self.assertEqual(self.track.points_count, 3)
        self.assertEqual(self.read(self.track.gpx_file), GPX)

    def test_download_original_returns_original_bytes_after_edit(self):
        self.edit()
        response = self.client.get(reverse("tracks:original", args=[self.track.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), GPX)

    def test_restore_requires_owner_and_explicit_post(self):
        self.edit()
        url = reverse("tracks:restore", args=[self.track.public_id])
        self.assertEqual(self.client.post(url).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user("stranger"))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url), "Восстановить из архива")
        self.track.refresh_from_db()
        self.assertEqual(self.track.points_count, 2)
        self.assertRedirects(self.client.post(url), self.track.get_absolute_url())
        self.track.refresh_from_db()
        self.assertEqual(self.track.points_count, 3)

    def test_missing_archive_does_not_replace_working_file(self):
        self.edit()
        previous = self.track.gpx_file.name
        with patch("django.db.models.fields.files.FieldFile.open", side_effect=OSError("missing")):
            self.client.force_login(self.owner)
            response = self.client.post(reverse("tracks:restore", args=[self.track.public_id]))
        self.assertContains(response, "Не удалось восстановить архив")
        self.track.refresh_from_db()
        self.assertEqual(self.track.gpx_file.name, previous)
        self.assertEqual(self.track.points_count, 2)
