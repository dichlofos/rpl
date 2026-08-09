from collections import defaultdict

from django.db import connection, transaction

from .models import TrackRegion


def classify_track(track):
    """Classify a track by its length inside every intersected admin region."""
    if not track.geometry:
        TrackRegion.objects.filter(track=track).delete()
        return []

    sql = """
        SELECT r.id,
               ST_Length(
                   ST_Intersection(t.geometry, r.geometry)::geography
               ) AS distance_m,
               ST_Length(t.geometry::geography) AS total_m,
               r.admin_level
          FROM tracks_track t
          JOIN regions_region r
            ON r.is_active AND ST_Intersects(t.geometry, r.geometry)
         WHERE t.id = %s
           AND NOT ST_IsEmpty(ST_Intersection(t.geometry, r.geometry))
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [track.pk])
        rows = cursor.fetchall()

    by_level = defaultdict(list)
    for region_id, distance_m, total_m, admin_level in rows:
        if distance_m and total_m:
            by_level[admin_level].append((region_id, float(distance_m), float(total_m)))

    links = []
    for level_rows in by_level.values():
        primary_id = max(level_rows, key=lambda row: row[1])[0]
        links.extend(
            TrackRegion(
                track=track,
                region_id=region_id,
                distance_m=distance_m,
                coverage_ratio=min(distance_m / total_m, 1.0),
                is_primary=region_id == primary_id,
            )
            for region_id, distance_m, total_m in level_rows
        )

    with transaction.atomic():
        TrackRegion.objects.filter(track=track).delete()
        TrackRegion.objects.bulk_create(links)
    return links


def classify_tracks_intersecting(geometry):
    return classify_tracks_intersecting_many([geometry])


def classify_tracks_intersecting_many(geometries):
    from tracks.models import Track

    track_ids = set()
    for geometry in geometries:
        track_ids.update(
            Track.objects.filter(geometry__intersects=geometry).values_list("id", flat=True)
        )
    return sum(len(classify_track(track)) for track in Track.objects.filter(id__in=track_ids))
