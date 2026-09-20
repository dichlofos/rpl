from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timezone

from .metadata import PhotoMetadata


@dataclass(frozen=True)
class SimilarityStack:
    photos: tuple[PhotoMetadata, ...]

    @property
    def sharpest(self):
        return max(self.photos, key=lambda photo: photo.sharpness)

    @property
    def kind(self):
        hashes = {photo.content_sha256 for photo in self.photos}
        return "exact" if len(hashes) == 1 else "visual"


@dataclass(frozen=True)
class TemporalEpisode:
    photos: tuple[PhotoMetadata, ...]

    @property
    def started_at(self):
        return self.photos[0].captured_at

    @property
    def finished_at(self):
        return self.photos[-1].captured_at

    @property
    def duration_seconds(self):
        if self.started_at is None or self.finished_at is None:
            return 0.0
        return _timestamp(self.finished_at) - _timestamp(self.started_at)


def hamming_distance(left, right):
    if len(left) != len(right):
        raise ValueError("Перцептивные хеши имеют разную длину.")
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _timestamp(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _aspect_ratios_match(left, right, tolerance=0.03):
    return abs(left.aspect_ratio - right.aspect_ratio) <= tolerance


def _matches(
    photo,
    representative,
    window_seconds,
    difference_hash_distance,
    perceptual_hash_distance,
):
    if photo.content_sha256 == representative.content_sha256:
        return True
    if photo.camera != representative.camera:
        return False
    photo_time = _timestamp(photo.captured_at)
    representative_time = _timestamp(representative.captured_at)
    if photo_time is None or representative_time is None:
        return False
    if abs(photo_time - representative_time) > window_seconds:
        return False
    if not _aspect_ratios_match(photo, representative):
        return False
    difference_distance = hamming_distance(photo.difference_hash, representative.difference_hash)
    perceptual_distance = hamming_distance(photo.perceptual_hash, representative.perceptual_hash)
    return (
        difference_distance <= difference_hash_distance
        or perceptual_distance <= perceptual_hash_distance
    )


def group_similar_photos(
    photos,
    *,
    window_seconds=60,
    difference_hash_distance=24,
    perceptual_hash_distance=10,
):
    """Build conservative stacks by comparing with a fixed representative.

    Comparing against the representative rather than joining every matching pair
    prevents a weak A-B-C similarity chain from merging unrelated A and C.
    """
    ordered = sorted(
        photos,
        key=lambda photo: (
            _timestamp(photo.captured_at) is None,
            _timestamp(photo.captured_at) or 0,
            str(photo.path),
        ),
    )
    stacks = []
    exact_hashes = {}
    recent_by_camera = defaultdict(deque)
    for photo in ordered:
        exact_stack_index = exact_hashes.get(photo.content_sha256)
        if exact_stack_index is not None:
            stacks[exact_stack_index].append(photo)
            continue

        photo_time = _timestamp(photo.captured_at)
        candidates = recent_by_camera[photo.camera]
        if photo_time is not None:
            while candidates:
                representative_time = _timestamp(stacks[candidates[0]][0].captured_at)
                if representative_time is None or photo_time - representative_time > window_seconds:
                    candidates.popleft()
                else:
                    break

        matching_index = next(
            (
                index
                for index in candidates
                if _matches(
                    photo,
                    stacks[index][0],
                    window_seconds,
                    difference_hash_distance,
                    perceptual_hash_distance,
                )
            ),
            None,
        )
        if matching_index is not None:
            stacks[matching_index].append(photo)
            exact_hashes[photo.content_sha256] = matching_index
            continue

        matching_index = len(stacks)
        stacks.append([photo])
        exact_hashes[photo.content_sha256] = matching_index
        if photo_time is not None:
            candidates.append(matching_index)
    return [SimilarityStack(tuple(stack)) for stack in stacks]


def group_temporal_episodes(photos, *, gap_seconds=10):
    """Group consecutive shots while keeping cameras independent."""
    photos_by_camera = defaultdict(list)
    without_time = []
    for photo in photos:
        if photo.captured_at is None:
            without_time.append(photo)
        else:
            photos_by_camera[photo.camera].append(photo)

    episodes = []
    for camera_photos in photos_by_camera.values():
        camera_photos.sort(key=lambda photo: (_timestamp(photo.captured_at), str(photo.path)))
        current = []
        previous_time = None
        for photo in camera_photos:
            photo_time = _timestamp(photo.captured_at)
            if current and photo_time - previous_time > gap_seconds:
                episodes.append(TemporalEpisode(tuple(current)))
                current = []
            current.append(photo)
            previous_time = photo_time
        if current:
            episodes.append(TemporalEpisode(tuple(current)))

    episodes.extend(TemporalEpisode((photo,)) for photo in without_time)
    episodes.sort(
        key=lambda episode: (
            _timestamp(episode.started_at) is None,
            _timestamp(episode.started_at) or 0,
            str(episode.photos[0].path),
        )
    )
    return episodes
