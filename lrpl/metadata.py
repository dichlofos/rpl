import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

JPEG_SUFFIXES = {".jpg", ".jpeg"}

TAG_MAKE = 271
TAG_MODEL = 272
TAG_DATETIME = 306
TAG_GPS_INFO = 34853
TAG_DATETIME_ORIGINAL = 36867
TAG_OFFSET_TIME = 36880
TAG_OFFSET_TIME_ORIGINAL = 36881
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
    content_sha256: str
    width: int
    height: int
    captured_at: datetime | None
    timezone_explicit: bool
    camera: str
    latitude: float | None
    longitude: float | None
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


def parse_exif_datetime(value, offset=None):
    if not value:
        return None, False
    try:
        captured_at = datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
    except ValueError as exc:
        raise InvalidPhoto(f"Некорректное время EXIF: {value}") from exc
    if not offset:
        return captured_at, False
    match = OFFSET_RE.fullmatch(str(offset))
    if not match:
        raise InvalidPhoto(f"Некорректный часовой пояс EXIF: {offset}")
    delta = timedelta(
        hours=int(match.group("hours")), minutes=int(match.group("minutes"))
    )
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
        raise InvalidPhoto(
            "GPS-координаты EXIF выходят за допустимые границы."
        )
    return latitude, longitude


def difference_hash(image, size=16):
    grayscale = image.convert("L").resize((size + 1, size), RESAMPLING.LANCZOS)
    pixels = list(grayscale.getdata())
    value = 0
    for row in range(size):
        offset = row * (size + 1)
        for column in range(size):
            value = (value << 1) | (
                pixels[offset + column] > pixels[offset + column + 1]
            )
    return f"{value:0{size * size // 4}x}"


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
            if source.format != "JPEG":
                raise InvalidPhoto("Поддерживаются только JPEG-файлы.")
            exif = source.getexif()
            captured_at, timezone_explicit = parse_exif_datetime(
                exif.get(TAG_DATETIME_ORIGINAL) or exif.get(TAG_DATETIME),
                exif.get(TAG_OFFSET_TIME_ORIGINAL) or exif.get(TAG_OFFSET_TIME),
            )
            latitude, longitude = _gps_coordinates(exif)
            camera_parts = [
                str(value).strip()
                for value in (
                    exif.get(TAG_MAKE),
                    exif.get(TAG_MODEL),
                    exif.get(TAG_BODY_SERIAL_NUMBER),
                )
                if value and str(value).strip()
            ]
            oriented = ImageOps.exif_transpose(source)
            oriented.load()
            width, height = oriented.size
            perceptual_hash = difference_hash(oriented)
            sharpness = sharpness_score(oriented)
    except InvalidPhoto:
        raise
    except Exception as exc:
        raise InvalidPhoto(f"Не удалось прочитать JPEG: {exc}") from exc

    return PhotoMetadata(
        path=path,
        content_sha256=file_sha256(path),
        width=width,
        height=height,
        captured_at=captured_at,
        timezone_explicit=timezone_explicit,
        camera=" ".join(camera_parts) or "unknown-camera",
        latitude=latitude,
        longitude=longitude,
        perceptual_hash=perceptual_hash,
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
