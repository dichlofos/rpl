import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from tracks.group_services import assign_track, delete_group, move_track
from tracks.models import Track, TrackGroup, TrackGroupMembership
from tracks.tests.test_tracks import GPX


class GroupTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = get_user_model().objects.create_user("owner")
        self.stranger = get_user_model().objects.create_user("stranger")
        self.group = TrackGroup.objects.create(name="Trip", owner=self.owner)
        self.other = TrackGroup.objects.create(name="Other trip", owner=self.owner)
        self.track = self.make_track("Day 1")
        self.second = self.make_track("Day 2")

    def make_track(self, name, owner=None):
        return Track.objects.create(
            name=name,
            owner=owner or self.owner,
            gpx_file=SimpleUploadedFile("track.gpx", GPX),
        )

    def ids(self, group=None):
        return list((group or self.group).memberships.values_list("track_id", flat=True))

    def test_assign_move_remove_and_reorder(self):
        assign_track(self.owner, self.track.pk, self.group.pk)
        assign_track(self.owner, self.second.pk, self.group.pk)
        assign_track(self.owner, self.track.pk, self.group.pk)
        self.assertEqual(self.ids(), [self.track.pk, self.second.pk])
        move_track(self.owner, self.group.pk, self.second.pk, "up")
        self.assertEqual(self.ids(), [self.second.pk, self.track.pk])
        move_track(self.owner, self.group.pk, self.second.pk, "down")
        self.assertEqual(self.ids(), [self.track.pk, self.second.pk])
        assign_track(self.owner, self.track.pk, self.other.pk)
        self.assertEqual(self.ids(), [self.second.pk])
        self.assertEqual(self.ids(self.other), [self.track.pk])
        assign_track(self.owner, self.track.pk, None)
        self.assertEqual(self.ids(self.other), [])

    def test_database_enforces_one_group_and_no_duplicate_membership(self):
        assign_track(self.owner, self.track.pk, self.group.pk)
        for group in [self.group, self.other]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                TrackGroupMembership.objects.create(track=self.track, group=group)

    def test_failed_transfer_preserves_previous_membership(self):
        assign_track(self.owner, self.track.pk, self.group.pk)
        foreign = TrackGroup.objects.create(name="Foreign", owner=self.stranger)
        with self.assertRaises(PermissionDenied):
            assign_track(self.owner, self.track.pk, foreign.pk)
        self.assertEqual(self.ids(), [self.track.pk])
        with self.assertRaises(ValidationError):
            assign_track(self.owner, self.track.pk, 9999999)
        self.assertEqual(self.ids(), [self.track.pk])

    def test_stale_remove_cannot_remove_track_from_new_group(self):
        assign_track(self.owner, self.track.pk, self.other.pk)
        with self.assertRaises(ValidationError):
            assign_track(self.owner, self.track.pk, None, expected_group_id=self.group.pk)
        self.assertEqual(self.ids(self.other), [self.track.pk])

    def test_foreign_track_and_reorder_are_forbidden(self):
        with self.assertRaises(PermissionDenied):
            assign_track(self.stranger, self.track.pk, self.group.pk)
        with self.assertRaises(PermissionDenied):
            move_track(self.stranger, self.group.pk, self.track.pk, "up")
        with self.assertRaises(PermissionDenied):
            delete_group(self.stranger, self.group.pk)

    def test_superuser_can_manage_but_cannot_mix_owners(self):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.test", "password")
        assign_track(admin, self.track.pk, self.group.pk)
        foreign = self.make_track("Foreign", owner=self.stranger)
        with self.assertRaises(ValidationError):
            assign_track(admin, foreign.pk, self.group.pk)

    def test_delete_group_preserves_tracks_and_both_files(self):
        assign_track(self.owner, self.track.pk, self.group.pk)
        delete_group(self.owner, self.group.pk)
        self.track.refresh_from_db()
        self.assertTrue(self.track.gpx_file.storage.exists(self.track.gpx_file.name))
        self.assertTrue(
            self.track.original_gpx_file.storage.exists(self.track.original_gpx_file.name)
        )
        self.assertFalse(self.track.groups.exists())

    def test_delete_track_removes_only_its_membership(self):
        assign_track(self.owner, self.track.pk, self.group.pk)
        assign_track(self.owner, self.second.pk, self.group.pk)
        self.track.delete()
        self.assertEqual(self.ids(), [self.second.pk])

    def test_public_group_map_preserves_tracks_segments_and_order(self):
        assign_track(self.owner, self.second.pk, self.group.pk)
        assign_track(self.owner, self.track.pk, self.group.pk)
        url = reverse("tracks:group-geojson", args=[self.group.public_id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        collection = response.json()
        self.assertEqual(collection["type"], "FeatureCollection")
        self.assertEqual(
            [f["id"] for f in collection["features"]],
            [str(self.second.public_id), str(self.track.public_id)],
        )
        self.assertEqual(collection["features"][0]["geometry"], self.second.geojson["geometry"])
        self.assertNotEqual(
            collection["features"][0]["properties"]["color"],
            collection["features"][1]["properties"]["color"],
        )
        page = self.client.get(self.group.get_absolute_url())
        self.assertContains(page, "Day 1")
        self.assertNotContains(page, "Добавить в группу")
        self.assertAlmostEqual(
            page.context["distance_km"], (self.track.distance_m + self.second.distance_m) / 1000
        )
        Track.objects.filter(pk=self.track.pk).update(distance_m=10)
        page = self.client.get(self.group.get_absolute_url())
        self.assertAlmostEqual(page.context["distance_km"], (10 + self.second.distance_m) / 1000)

    def test_empty_group(self):
        response = self.client.get(self.group.get_absolute_url())
        self.assertContains(response, "В группе пока нет треков")
        self.assertNotContains(response, 'id="group-map"')
        self.assertEqual(response.context["distance_km"], 0)

    def test_create_edit_add_reorder_remove_delete_via_ui(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("tracks:group-create"), {"name": "New", "description": "Trip"})
        group = TrackGroup.objects.get(name="New")
        self.assertEqual(group.owner, self.owner)
        self.client.post(
            reverse("tracks:group-edit", args=[group.public_id]),
            {"name": "Renamed", "description": ""},
        )
        self.client.post(
            reverse("tracks:group-add", args=[group.public_id]),
            {"track": str(self.track.public_id)},
        )
        self.client.post(
            reverse("tracks:group-add", args=[group.public_id]),
            {"track": str(self.second.public_id)},
        )
        url = reverse("tracks:group-member", args=[group.public_id, self.second.public_id])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {"action": "up"})
        self.assertEqual(self.ids(group), [self.second.pk, self.track.pk])
        self.client.post(url, {"action": "remove"})
        self.assertEqual(self.ids(group), [self.track.pk])
        delete_url = reverse("tracks:group-delete", args=[group.public_id])
        self.assertEqual(self.client.get(delete_url).status_code, 200)
        self.assertTrue(TrackGroup.objects.filter(pk=group.pk).exists())
        self.client.post(delete_url)
        self.assertFalse(TrackGroup.objects.filter(pk=group.pk).exists())

    def test_upload_to_group_and_track_group_selector(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("tracks:upload"),
            {
                "name": "Uploaded",
                "gpx_file": SimpleUploadedFile("day.gpx", GPX),
                "group": str(self.group.public_id),
            },
        )
        self.assertRedirects(response, self.group.get_absolute_url())
        track = Track.objects.get(name="Uploaded")
        self.assertEqual(list(track.groups.all()), [self.group])
        response = self.client.get(track.get_absolute_url())
        self.assertContains(response, self.group.get_absolute_url())
        self.client.post(
            reverse("tracks:group", args=[track.public_id]), {"group": str(self.other.public_id)}
        )
        self.assertEqual(list(track.groups.all()), [self.other])

    def test_foreign_groups_are_not_selectable_and_mutations_are_forbidden(self):
        self.client.force_login(self.stranger)
        for name in ["group-edit", "group-delete", "group-add"]:
            self.assertEqual(
                self.client.post(
                    reverse(f"tracks:{name}", args=[self.group.public_id]), {}
                ).status_code,
                403,
            )
        self.assertEqual(
            self.client.post(reverse("tracks:group", args=[self.track.public_id]), {}).status_code,
            403,
        )
        before = Track.objects.count()
        response = self.client.post(
            reverse("tracks:upload"),
            {
                "name": "Invalid",
                "gpx_file": SimpleUploadedFile("day.gpx", GPX),
                "group": str(self.group.public_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Track.objects.count(), before)

    def test_staff_cannot_modify_foreign_group_through_admin(self):
        self.stranger.is_staff = True
        self.stranger.save()
        self.stranger.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="tracks",
                codename__in=["view_trackgroup", "change_trackgroup", "delete_trackgroup"],
            )
        )
        self.client.force_login(self.stranger)
        self.client.post(
            reverse("admin:tracks_trackgroup_change", args=[self.group.pk]),
            {
                "name": "Hijacked",
                "description": "",
                "_save": "Save",
            },
        )
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Trip")
