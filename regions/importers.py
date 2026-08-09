import json
from dataclasses import dataclass
from pathlib import Path

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon


class InvalidOvertureData(ValueError):
    pass


@dataclass(frozen=True)
class Division:
    external_id: str
    parent_external_id: str
    name: str
    names: dict
    country_code: str
    region_code: str
    subtype: str
    admin_level: int | None
    version: int


@dataclass(frozen=True)
class RegionRecord:
    division: Division
    area_external_id: str
    geometry: MultiPolygon


def iter_geojson(path):
    path = Path(path)
    with path.open(encoding="utf-8") as source:
        prefix = source.read(4096)
        source.seek(0)
        if '"FeatureCollection"' in prefix:
            payload = json.load(source)
            yield from payload.get("features", [])
            return
        for line_number, line in enumerate(source, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise InvalidOvertureData(
                        f"Invalid GeoJSON sequence at line {line_number}"
                    ) from exc


def primary_name(names):
    if isinstance(names, dict):
        return names.get("primary") or next(iter((names.get("common") or {}).values()), "")
    return ""


def load_divisions(path):
    divisions = {}
    for feature in iter_geojson(path):
        properties = feature.get("properties") or {}
        external_id = feature.get("id") or properties.get("id")
        names = properties.get("names") or {}
        if not external_id or not primary_name(names):
            continue
        divisions[external_id] = Division(
            external_id=external_id,
            parent_external_id=properties.get("parent_division_id") or "",
            name=primary_name(names),
            names=names,
            country_code=properties.get("country") or "",
            region_code=properties.get("region") or "",
            subtype=properties.get("subtype") or "",
            admin_level=properties.get("admin_level"),
            version=properties.get("version") or 0,
        )
    return divisions


def load_region_records(divisions_path, areas_path, countries=None, admin_levels=None):
    divisions = load_divisions(divisions_path)
    countries = {country.upper() for country in countries or []}
    admin_levels = set(admin_levels or [])
    records = []
    for feature in iter_geojson(areas_path):
        properties = feature.get("properties") or {}
        if properties.get("is_land") is False:
            continue
        division_id = properties.get("division_id")
        division = divisions.get(division_id)
        if not division:
            names = properties.get("names") or {}
            if not division_id or not primary_name(names):
                continue
            division = Division(
                external_id=division_id,
                parent_external_id="",
                name=primary_name(names),
                names=names,
                country_code=properties.get("country") or "",
                region_code=properties.get("region") or "",
                subtype=properties.get("subtype") or "",
                admin_level=properties.get("admin_level"),
                version=properties.get("version") or 0,
            )
        if countries and division.country_code not in countries:
            continue
        if admin_levels and division.admin_level not in admin_levels:
            continue
        geometry_data = feature.get("geometry")
        if not geometry_data:
            continue
        geometry = GEOSGeometry(json.dumps(geometry_data), srid=4326)
        if isinstance(geometry, Polygon):
            geometry = MultiPolygon(geometry, srid=4326)
        if not isinstance(geometry, MultiPolygon) or geometry.empty:
            raise InvalidOvertureData(f"Invalid polygon for division {division_id}")
        if not geometry.valid:
            geometry = geometry.make_valid()
            if isinstance(geometry, Polygon):
                geometry = MultiPolygon(geometry, srid=4326)
        records.append(
            RegionRecord(
                division=division,
                area_external_id=feature.get("id") or properties.get("id") or "",
                geometry=geometry,
            )
        )
    return records
