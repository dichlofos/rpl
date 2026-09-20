import hashlib
import math
import re
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

JPEG_SUFFIXES = {".jpg", ".jpeg"}
JPEG_FORMATS = {"JPEG", "MPO"}
DIFFERENCE_HASH_VERSION = "dhash-256-v1"
PERCEPTUAL_HASH_VERSION = "phash-64-dct-v1"
SHARPNESS_VERSION = "edge-variance-v1"

TAG_MAKE = 271
TAG_MODEL = 272
TAG_DATETIME = 306
TAG_EXIF_IFD = 34665
TAG_GPS_INFO = 34853
TAG_DATETIME_ORIGINAL = 36867
TAG_OFFSET_TIME = 36880
TAG_OFFSET_TIME_ORIGINAL = 36881
TAG_SUBSEC_TIME_ORIGINAL = 37521
TAG_BODY_SERIAL_NUMBER = 42033

GPS_LATITUDE_REF = 1
GPS_LATITUDE = 2
GPS_LONGITUDE_REF = 3
GPS_LONGITUDE = 4

OFFSET_RE = re.compile(r"^(?P<sign>[+-])(?P<hours>\d{2}):?(?P<minutes>\d{2})$")
RESAMPLING = getattr(Image, "Resampling", Image)


class InvalidPhoto(ValueError):
    pass


@dataclass(frozen=True)
class PhotoMetadata:
    path: Path
    source_format: str
    content_sha256: str
    width: int
    height: int
    captured_at: datetime | None
    timezone_explicit: bool
    camera: str
    latitude: float | None
    longitude: float | None
    difference_hash: str
    perceptual_hash: str
    sharpness: float

    @property
    def aspect_ratio(self):
        return self.width / self.height

    def as_json(self):
        result = asdict(self)
        result["path"] = str(self.path)
        result["captured_at"] = self.captured_at.isoformat() if self.captured_at else None
        return result


@dataclass(frozen=True)
class PhotoClockMetadata:
    path: Path
    captured_at: datetime | None
    timezone_explicit: bool
    camera: str
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class ScanFailure:
    path: Path
    error: str

    def as_json(self):
        return {"path": str(self.path), "error": self.error}


def file_sha256(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_exif_datetime(value, offset=None, subsecond=None):
    if not value:
        return None, False
    try:
        # A missing EXIF offset deliberately means camera wall time, not UTC.
        captured_at = datetime.strptime(  # noqa: DTZ007
            str(value), "%Y:%m:%d %H:%M:%S"
        )
    except ValueError as exc:
        raise InvalidPhoto(f"Некорректное время EXIF: {value}") from exc
    if subsecond is not None:
        digits = "".join(character for character in str(subsecond) if character.isdigit())
        if digits:
            captured_at = captured_at.replace(microsecond=int((digits + "000000")[:6]))
    if not offset:
        return captured_at, False
    match = OFFSET_RE.fullmatch(str(offset))
    if not match:
        raise InvalidPhoto(f"Некорректный часовой пояс EXIF: {offset}")
    delta = timedelta(hours=int(match.group("hours")), minutes=int(match.group("minutes")))
    if match.group("sign") == "-":
        delta = -delta
    return captured_at.replace(tzinfo=timezone(delta)), True


def _ratio(value):
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise InvalidPhoto("Некорректные GPS-координаты в EXIF.") from exc


def _degrees(value):
    if not value or len(value) != 3:
        raise InvalidPhoto("Некорректные GPS-координаты в EXIF.")
    degrees, minutes, seconds = (_ratio(item) for item in value)
    return degrees + minutes / 60 + seconds / 3600


def _gps_coordinates(exif):
    if TAG_GPS_INFO not in exif:
        return None, None
    try:
        gps = exif.get_ifd(TAG_GPS_INFO)
    except (AttributeError, KeyError, TypeError):
        gps = exif.get(TAG_GPS_INFO)
    if not isinstance(gps, dict):
        return None, None
    latitude_value = gps.get(GPS_LATITUDE)
    longitude_value = gps.get(GPS_LONGITUDE)
    if not latitude_value or not longitude_value:
        return None, None
    latitude = _degrees(latitude_value)
    longitude = _degrees(longitude_value)
    latitude_ref = gps.get(GPS_LATITUDE_REF, "N")
    longitude_ref = gps.get(GPS_LONGITUDE_REF, "E")
    if isinstance(latitude_ref, bytes):
        latitude_ref = latitude_ref.decode("ascii", errors="replace")
    if isinstance(longitude_ref, bytes):
        longitude_ref = longitude_ref.decode("ascii", errors="replace")
    if str(latitude_ref).upper() == "S":
        latitude = -latitude
    if str(longitude_ref).upper() == "W":
        longitude = -longitude
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise InvalidPhoto("GPS-координаты EXIF выходят за допустимые границы.")
    return latitude, longitude


def _nested_exif(exif):
    try:
        result = exif.get_ifd(TAG_EXIF_IFD)
    except (AttributeError, KeyError, TypeError):
        return {}
    return result if isinstance(result, dict) else {}


def _clock_metadata(path, source):
    exif = source.getexif()
    details = _nested_exif(exif)
    captured_at, timezone_explicit = parse_exif_datetime(
        details.get(TAG_DATETIME_ORIGINAL)
        or exif.get(TAG_DATETIME_ORIGINAL)
        or exif.get(TAG_DATETIME),
        details.get(TAG_OFFSET_TIME_ORIGINAL)
        or exif.get(TAG_OFFSET_TIME_ORIGINAL)
        or details.get(TAG_OFFSET_TIME)
        or exif.get(TAG_OFFSET_TIME),
        details.get(TAG_SUBSEC_TIME_ORIGINAL) or exif.get(TAG_SUBSEC_TIME_ORIGINAL),
    )
    camera_parts = [
        str(value).strip()
        for value in (
            exif.get(TAG_MAKE),
            exif.get(TAG_MODEL),
            details.get(TAG_BODY_SERIAL_NUMBER) or exif.get(TAG_BODY_SERIAL_NUMBER),
        )
        if value and str(value).strip()
    ]
    latitude, longitude = _gps_coordinates(exif)
    return PhotoClockMetadata(
        path=Path(path),
        captured_at=captured_at,
        timezone_explicit=timezone_explicit,
        camera=" ".join(camera_parts) or "unknown-camera",
        latitude=latitude,
        longitude=longitude,
    )


def difference_hash(image, size=16):
    grayscale = image.convert("L").resize((size + 1, size), RESAMPLING.LANCZOS)
    pixels = list(grayscale.getdata())
    value = 0
    for row in range(size):
        offset = row * (size + 1)
        for column in range(size):
            value = (value << 1) | (pixels[offset + column] > pixels[offset + column + 1])
    return f"{value:0{size * size // 4}x}"


@cache
def _dct_cosines(sample_size, hash_size):
    return tuple(
        tuple(
            math.cos(math.pi * (2 * coordinate + 1) * frequency / (2 * sample_size))
            for coordinate in range(sample_size)
        )
        for frequency in range(hash_size)
    )


def perceptual_hash(image, hash_size=8, high_frequency_factor=4):
    """Return a compact DCT pHash without requiring NumPy or SciPy."""
    sample_size = hash_size * high_frequency_factor
    grayscale = image.convert("L").resize((sample_size, sample_size), RESAMPLING.LANCZOS)
    pixels = list(grayscale.getdata())
    cosines = _dct_cosines(sample_size, hash_size)
    row_coefficients = [
        [
            sum(
                pixels[row * sample_size + column] * cosines[frequency][column]
                for column in range(sample_size)
            )
            for frequency in range(hash_size)
        ]
        for row in range(sample_size)
    ]
    coefficients = [
        sum(
            row_coefficients[row][horizontal] * cosines[vertical][row] for row in range(sample_size)
        )
        for vertical in range(hash_size)
        for horizontal in range(hash_size)
    ]
    median = statistics.median(coefficients[1:])
    value = 0
    for index, coefficient in enumerate(coefficients):
        value = (value << 1) | (index != 0 and coefficient > median)
    return f"{value:0{hash_size * hash_size // 4}x}"


def sharpness_score(image, max_side=512):
    grayscale = image.convert("L")
    grayscale.thumbnail((max_side, max_side), RESAMPLING.LANCZOS)
    if grayscale.width < 3 or grayscale.height < 3:
        return 0.0
    edges = grayscale.filter(ImageFilter.FIND_EDGES).crop(
        (1, 1, grayscale.width - 1, grayscale.height - 1)
    )
    return float(ImageStat.Stat(edges).var[0])


def read_photo(path):
    path = Path(path)
    try:
        with Image.open(path) as source:
            if source.format not in JPEG_FORMATS:
                raise InvalidPhoto("Поддерживаются только JPEG и JPEG/MPO-файлы.")
            source_format = source.format
            clock = _clock_metadata(path, source)
            oriented = ImageOps.exif_transpose(source)
            oriented.load()
            width, height = oriented.size
            dhash = difference_hash(oriented)
            phash = perceptual_hash(oriented)
            sharpness = sharpness_score(oriented)
    except InvalidPhoto:
        raise
    except Exception as exc:
        raise InvalidPhoto(f"Не удалось прочитать JPEG: {exc}") from exc

    return PhotoMetadata(
        path=path,
        source_format=source_format,
        content_sha256=file_sha256(path),
        width=width,
        height=height,
        captured_at=clock.captured_at,
        timezone_explicit=clock.timezone_explicit,
        camera=clock.camera,
        latitude=clock.latitude,
        longitude=clock.longitude,
        difference_hash=dhash,
        perceptual_hash=phash,
        sharpness=sharpness,
    )


def scan_directory(root):
    root = Path(root)
    photos = []
    failures = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.suffix.lower() not in JPEG_SUFFIXES:
            continue
        try:
            photos.append(read_photo(path))
        except InvalidPhoto as exc:
            failures.append(ScanFailure(path=path, error=str(exc)))
    return photos, failures


def read_photo_clock(path):
    path = Path(path)
    try:
        with Image.open(path) as source:
            if source.format not in JPEG_FORMATS:
                raise InvalidPhoto("Поддерживаются только JPEG и JPEG/MPO-файлы.")
            return _clock_metadata(path, source)
    except InvalidPhoto:
        raise
    except Exception as exc:
        raise InvalidPhoto(f"Не удалось прочитать JPEG: {exc}") from exc


def scan_photo_clocks(root):
    root = Path(root)
    photos = []
    failures = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.suffix.lower() not in JPEG_SUFFIXES:
            continue
        try:
            photos.append(read_photo_clock(path))
        except InvalidPhoto as exc:
            failures.append(ScanFailure(path=path, error=str(exc)))
    return photos, failures
