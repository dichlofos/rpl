import tempfile

from django.core.files.base import ContentFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings

from tracks.models import Track
from tracks.tests.test_tracks import GPX


class ArchiveMigrationTests(TransactionTestCase):
    def test_existing_files_are_copied_without_changing_working_content(self):
        before = [("tracks", "0004_track_geometry")]
        after = [("tracks", "0005_trackgroup_trackgroupmembership_and_more")]
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            executor = MigrationExecutor(connection)
            executor.migrate(before)
            try:
                OldTrack = executor.loader.project_state(before).apps.get_model("tracks", "Track")
                track = OldTrack(name="Existing")
                track.gpx_file.save("existing.gpx", ContentFile(GPX), save=False)
                track.save()
                pk = track.pk
                working_name = track.gpx_file.name
            finally:
                MigrationExecutor(connection).migrate(after)
            track = Track.objects.get(pk=pk)
            self.assertEqual(track.gpx_file.name, working_name)
            self.assertNotEqual(track.original_gpx_file.name, working_name)
            with track.original_gpx_file.open("rb") as archived:
                self.assertEqual(archived.read(), GPX)
            with track.gpx_file.open("rb") as current:
                self.assertEqual(current.read(), GPX)
            self.assertFalse(track.groups.exists())
