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


def _matches(photo, representative, window_seconds, hash_distance):
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
    return hamming_distance(photo.perceptual_hash, representative.perceptual_hash) <= hash_distance


def group_similar_photos(photos, *, window_seconds=60, hash_distance=24):
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
                if _matches(photo, stacks[index][0], window_seconds, hash_distance)
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
