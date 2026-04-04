from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shapely.geometry import mapping
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid
from shapely.wkt import dumps as dump_wkt
from shapely.wkt import loads as load_wkt

from .models import AoiContext, IntersectionResult, SearchArea

try:
	from pyproj import Geod

	_GEOD = Geod(ellps="WGS84")
except Exception:
	_GEOD = None


def load_aoi_geometry(geojson_path: Path) -> BaseGeometry:
	geometries = _load_geometries_from_geojson(geojson_path)
	if not geometries:
		raise ValueError(f"No valid geometries found in {geojson_path}")
	merged = geometries[0]
	for geometry in geometries[1:]:
		merged = merged.union(geometry)
	return normalize_geometry(merged)


def build_search_areas(geojson_path: Path, envelope_only: bool = False) -> tuple[SearchArea, ...]:
	geometries = _load_geometries_from_geojson(geojson_path)
	search_areas: list[SearchArea] = []
	for feature_index, geometry in enumerate(geometries):
		parts = [geometry] if geometry.geom_type == "Polygon" else list(getattr(geometry, "geoms", []))
		for part_index, part in enumerate(parts, start=1):
			if part.is_empty or part.geom_type != "Polygon":
				continue
			query_geometry = normalize_geometry(part.envelope if envelope_only else part)
			search_areas.append(
				SearchArea(
					query_name=f"feature_{feature_index}_part_{part_index}",
					feature_index=feature_index,
					geometry_wkt=dump_wkt(query_geometry),
					geometry_geojson=mapping(query_geometry),
				)
			)
	if not search_areas:
		raise ValueError(f"No polygon search areas found in {geojson_path}")
	return tuple(search_areas)


def load_aoi_context(search_aoi_path: Path, validation_aoi_path: Path) -> AoiContext:
	search_geometry = load_aoi_geometry(search_aoi_path)
	validation_geometry = load_aoi_geometry(validation_aoi_path)
	return AoiContext(
		search_areas=build_search_areas(search_aoi_path),
		search_geometry_wkt=dump_wkt(search_geometry),
		validation_geometry_wkt=dump_wkt(validation_geometry),
	)


def normalize_geometry(geometry: BaseGeometry) -> BaseGeometry:
	if geometry.is_empty:
		return geometry
	valid = make_valid(geometry)
	if valid.is_empty:
		return valid
	return valid


def compute_intersection_metrics(footprint_wkt: str, aoi_geometry: BaseGeometry) -> IntersectionResult:
	footprint = normalize_geometry(load_wkt(footprint_wkt))
	if footprint.is_empty or aoi_geometry.is_empty:
		return IntersectionResult(False, 0.0, 0.0, 0.0, None)
	intersection = normalize_geometry(footprint.intersection(aoi_geometry))
	footprint_area = area_square_km(footprint)
	intersection_area = area_square_km(intersection) if not intersection.is_empty else 0.0
	intersection_fraction = 0.0 if footprint_area <= 0 else min(1.0, intersection_area / footprint_area)
	return IntersectionResult(
		intersects=not intersection.is_empty and intersection_area > 0.0,
		intersection_area_km2=intersection_area,
		intersection_fraction=intersection_fraction,
		footprint_area_km2=footprint_area,
		intersection_wkt=None if intersection.is_empty else dump_wkt(intersection),
	)


def area_square_km(geometry: BaseGeometry) -> float:
	if geometry.is_empty:
		return 0.0
	if _GEOD is not None:
		area, _ = _GEOD.geometry_area_perimeter(geometry)
		return abs(area) / 1_000_000.0
	return abs(geometry.area)


def _load_geometries_from_geojson(geojson_path: Path) -> list[BaseGeometry]:
	payload = json.loads(geojson_path.read_text(encoding="utf-8"))
	features = payload.get("features", []) if payload.get("type") == "FeatureCollection" else [payload]
	return [normalize_geometry(shape(feature["geometry"])) for feature in features if feature.get("geometry")]