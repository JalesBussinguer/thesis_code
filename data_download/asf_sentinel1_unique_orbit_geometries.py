"""Gera geometrias de orbitas unicas do Sentinel-1 a partir da Search API do ASF.

O script consulta cenas Sentinel-1 no ASF, agrega footprints por chave de orbita
e exporta os resultados em GeoJSON e CSV.

Uso padrao:
    python data_download/asf_sentinel1_unique_orbit_geometries.py
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import requests
from shapely.geometry import box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.validation import make_valid
from shapely.wkb import loads as load_wkb
from shapely.wkt import loads as load_wkt_text

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "datasets" / "asf_orbits_sentinel1"
SEARCH_URL = "https://api.daac.asf.alaska.edu/services/search/param"
REQUEST_TIMEOUT = 120
DEFAULT_START = "2025-11-01T00:00:00Z"
DEFAULT_END = "2026-03-31T23:59:59Z"
DEFAULT_PROCESSING_LEVEL = "SLC"
DEFAULT_PLATFORMS = ["Sentinel-1A", "Sentinel-1C"]

logging.getLogger("urllib3").setLevel(logging.ERROR)


# ======================= #
# CONFIGURACAO DO USUARIO #
# ======================= #
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
SEARCH_GEOJSON_PATH = "datasets/cerrado_bbox.geojson"
VALIDATION_GEOJSON_PATH = "datasets/cerrado_border.geojson"
DATE_START = None
DATE_END = None
PROCESSING_LEVEL = DEFAULT_PROCESSING_LEVEL
WINDOW_MONTHS = 1
PLATFORM_FILTERS = DEFAULT_PLATFORMS.copy()
RELATIVE_ORBIT_FILTERS = list(range(1, 176))
FLIGHT_DIRECTION = None  # "ASCENDING" ou "DESCENDING"
MAX_RESULTS_PER_QUERY = None
WRITE_SCENE_MANIFEST = False
AOI_SIMPLIFY_TOLERANCE = 0.0004
MAX_WKT_CHARS = 6000
MAX_SPLIT_DEPTH = 6


@dataclass(frozen=True)
class SearchArea:
	query_name: str
	feature_index: int | None
	wkt: str | None


@dataclass(frozen=True)
class SearchTask:
	query_name: str
	feature_index: int | None
	wkt: str | None
	start: str
	end: str
	platform: str | None
	relative_orbit: int | None


def read_filter_geometry(geojson_path: str) -> Any:
	gdf = gpd.read_file(geojson_path)
	if gdf.empty:
		raise ValueError("O GeoJSON nao contem feicoes.")
	if gdf.crs is None:
		raise ValueError("O GeoJSON precisa ter CRS definido.")

	gdf = gdf.to_crs("EPSG:4326")
	gdf = gdf.loc[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()
	if gdf.empty:
		raise ValueError("Nenhuma geometria valida foi encontrada no GeoJSON.")

	gdf["geometry"] = gdf.geometry.apply(make_valid)
	return unary_union(list(gdf.geometry))


def validate_configuration() -> None:
	if not OUTPUT_DIR:
		raise ValueError("Defina OUTPUT_DIR com o diretorio de saida.")
	if not PROCESSING_LEVEL:
		raise ValueError("Defina PROCESSING_LEVEL para a busca Sentinel-1.")
	if not PLATFORM_FILTERS:
		raise ValueError("Defina PLATFORM_FILTERS com pelo menos um satelite Sentinel-1.")
	if not RELATIVE_ORBIT_FILTERS:
		raise ValueError("Defina RELATIVE_ORBIT_FILTERS com pelo menos uma orbita relativa.")


def sanitize_name(value: str) -> str:
	return "_".join(part for part in str(value).strip().replace("/", "_").split() if part) or "unnamed"


def parse_iso_datetime(value: str) -> datetime:
	return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def format_iso_datetime(value: datetime) -> str:
	return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def add_months(value: datetime, months: int) -> datetime:
	month_index = value.month - 1 + months
	year = value.year + month_index // 12
	month = month_index % 12 + 1
	day = min(
		value.day,
		[31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1],
	)
	return value.replace(year=year, month=month, day=day)


def build_time_windows() -> list[tuple[str, str]]:
	start_value = DATE_START or DEFAULT_START
	end_value = DATE_END or DEFAULT_END
	start_dt = parse_iso_datetime(start_value)
	end_dt = parse_iso_datetime(end_value)
	if start_dt >= end_dt:
		raise ValueError("DATE_START precisa ser menor que DATE_END.")

	if WINDOW_MONTHS <= 0:
		return [(format_iso_datetime(start_dt), format_iso_datetime(end_dt))]

	windows: list[tuple[str, str]] = []
	current_start = start_dt
	while current_start < end_dt:
		next_start = add_months(current_start, WINDOW_MONTHS)
		current_end = min(next_start, end_dt)
		windows.append((format_iso_datetime(current_start), format_iso_datetime(current_end)))
		current_start = current_end
	return windows


def read_search_areas(geojson_path: str | None) -> list[SearchArea]:
	if not geojson_path:
		return [SearchArea(query_name="global", feature_index=None, wkt=None)]

	gdf = gpd.read_file(geojson_path)
	if gdf.empty:
		raise ValueError("O GeoJSON nao contem feicoes.")
	if gdf.crs is None:
		raise ValueError("O GeoJSON precisa ter CRS definido.")

	gdf = gdf.to_crs("EPSG:4326")
	gdf = gdf.loc[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()
	if gdf.empty:
		raise ValueError("Nenhuma geometria valida foi encontrada no GeoJSON.")

	gdf["geometry"] = gdf.geometry.apply(make_valid)
	areas: list[SearchArea] = []
	for idx, row in gdf.iterrows():
		geom = row.geometry
		parts = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
		for part_index, part in enumerate(parts, start=1):
			if part.geom_type != "Polygon":
				continue
			if AOI_SIMPLIFY_TOLERANCE:
				part = part.simplify(AOI_SIMPLIFY_TOLERANCE, preserve_topology=True)
			query_part = orient(part.envelope, sign=1.0)
			for split_index, query_polygon in enumerate(split_geometry_for_queries(query_part), start=1):
				areas.append(
					SearchArea(
						query_name=sanitize_name(f"feature_{idx}_part_{part_index}_split_{split_index}"),
						feature_index=idx,
						wkt=query_polygon.wkt,
					)
				)

	if not areas:
		raise ValueError("Nenhum Polygon ou MultiPolygon foi encontrado no GeoJSON.")
	return areas


def split_geometry_for_queries(geometry: Any, depth: int = 0) -> list[Any]:
	geometry = orient(make_valid(geometry), sign=1.0)
	if geometry.is_empty:
		return []
	if len(geometry.wkt) <= MAX_WKT_CHARS or depth >= MAX_SPLIT_DEPTH:
		return [geometry]

	min_x, min_y, max_x, max_y = geometry.bounds
	mid_x = (min_x + max_x) / 2
	mid_y = (min_y + max_y) / 2
	quadrants = [
		box(min_x, min_y, mid_x, mid_y),
		box(mid_x, min_y, max_x, mid_y),
		box(min_x, mid_y, mid_x, max_y),
		box(mid_x, mid_y, max_x, max_y),
	]

	pieces: list[Any] = []
	for quadrant in quadrants:
		intersection = geometry.intersection(quadrant)
		if intersection.is_empty:
			continue
		geoms = [intersection] if intersection.geom_type == "Polygon" else list(getattr(intersection, "geoms", []))
		for item in geoms:
			if item.is_empty or item.geom_type != "Polygon":
				continue
			pieces.extend(split_geometry_for_queries(item, depth + 1))

	return pieces or [geometry]


def iter_search_tasks(areas: list[SearchArea]) -> Iterable[SearchTask]:
	for area in areas:
		for start, end in build_time_windows():
			for platform in PLATFORM_FILTERS:
				for relative_orbit in RELATIVE_ORBIT_FILTERS:
					yield SearchTask(
						query_name=area.query_name,
						feature_index=area.feature_index,
						wkt=area.wkt,
						start=start,
						end=end,
						platform=platform,
						relative_orbit=relative_orbit,
					)


def search_products(task: SearchTask) -> list[dict[str, Any]]:
	params: dict[str, Any] = {
		"dataset": "SENTINEL-1",
		"processingLevel": PROCESSING_LEVEL,
		"platform": task.platform,
		"start": task.start,
		"end": task.end,
		"output": "json",
		"relativeOrbit": task.relative_orbit,
	}
	if task.wkt:
		params["intersectsWith"] = task.wkt
	if FLIGHT_DIRECTION:
		params["flightDirection"] = FLIGHT_DIRECTION
	if MAX_RESULTS_PER_QUERY is not None:
		params["maxResults"] = MAX_RESULTS_PER_QUERY

	response = requests.get(SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
	response.raise_for_status()
	payload = response.json()
	if isinstance(payload, list):
		if payload and isinstance(payload[0], list):
			return [item for group in payload if isinstance(group, list) for item in group if isinstance(item, dict)]
		return [item for item in payload if isinstance(item, dict)]
	raise ValueError("Resposta inesperada da Search API do ASF.")


def build_orbit_key(properties: dict[str, Any]) -> str:
	path_number = properties.get("pathNumber") or properties.get("track") or properties.get("relativeOrbit")
	parts = [
		"SENTINEL-1",
		sanitize_name(properties.get("platform") or "SENTINEL-1"),
		sanitize_name(properties.get("flightDirection") or "UNKNOWN"),
		f"PATH_{path_number or 'NA'}",
	]
	beam_mode = properties.get("beamModeType")
	if beam_mode:
		parts.append(sanitize_name(beam_mode))
	return "__".join(parts)


def format_frame_numbers(frame_numbers: set[Any]) -> str | None:
	values = []
	for value in frame_numbers:
		if value in (None, ""):
			continue
		values.append(str(value))
	if not values:
		return None
	return ",".join(sorted(values, key=lambda item: (len(item), item)))


def write_csv(csv_path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
	csv_path.parent.mkdir(parents=True, exist_ok=True)
	with csv_path.open("w", encoding="utf-8", newline="") as csvfile:
		writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)


def collect_unique_orbit_geometries() -> tuple[gpd.GeoDataFrame, list[dict[str, Any]]]:
	filter_geometry = read_filter_geometry(VALIDATION_GEOJSON_PATH) if VALIDATION_GEOJSON_PATH else None
	areas = read_search_areas(SEARCH_GEOJSON_PATH)
	orbit_map: dict[str, dict[str, Any]] = {}
	scene_rows: list[dict[str, Any]] = []

	for task_index, task in enumerate(iter_search_tasks(areas), start=1):
		try:
			results = search_products(task)
		except Exception as error:
			print(
				f"[SENTINEL-1] tarefa {task_index} falhou em {task.start} -> {task.end}, platform={task.platform}, relativeOrbit={task.relative_orbit}: {error}",
				flush=True,
			)
			continue

		if len(results) == 0:
			continue

		print(
			f"[SENTINEL-1] tarefa {task_index}: {len(results)} cena(s) em {task.start} -> {task.end}, platform={task.platform}, relativeOrbit={task.relative_orbit}",
			flush=True,
		)

		for feature in results:
			properties = dict(feature)
			try:
				geometry_wkt_text = properties.get("stringFootprint")
				if not geometry_wkt_text:
					continue
				orbit_key = build_orbit_key(properties)
				geometry = load_wkt_text(geometry_wkt_text)
				if task.platform and properties.get("platform") != task.platform:
					continue
				if filter_geometry is not None and not geometry.intersects(filter_geometry):
					continue
				geometry_wkb = geometry.wkb_hex
				path_number = properties.get("pathNumber") or properties.get("track") or properties.get("relativeOrbit")
				example_scene = properties.get("sceneName") or properties.get("granuleName") or properties.get("fileName")
				entry = orbit_map.setdefault(
					orbit_key,
					{
						"orbit_key": orbit_key,
						"dataset": "SENTINEL-1",
						"platform": properties.get("platform"),
						"processing_level": properties.get("processingLevel"),
						"flight_direction": properties.get("flightDirection"),
						"path_number": path_number,
						"frame_numbers": set(),
						"beam_mode": properties.get("beamModeType"),
						"scene_count": 0,
						"unique_footprints": set(),
						"first_start_time": None,
						"last_stop_time": None,
						"example_scene": example_scene,
					},
				)

				entry["scene_count"] += 1
				entry["unique_footprints"].add(geometry_wkb)
				entry["frame_numbers"].add(properties.get("frameNumber"))

				start_time = properties.get("startTime")
				stop_time = properties.get("stopTime")
				if start_time and (entry["first_start_time"] is None or start_time < entry["first_start_time"]):
					entry["first_start_time"] = start_time
				if stop_time and (entry["last_stop_time"] is None or stop_time > entry["last_stop_time"]):
					entry["last_stop_time"] = stop_time

				if WRITE_SCENE_MANIFEST:
					scene_rows.append(
						{
							"orbit_key": orbit_key,
							"dataset": "SENTINEL-1",
							"platform": properties.get("platform"),
							"scene_name": example_scene,
							"file_name": properties.get("fileName"),
							"path_number": path_number,
							"absolute_orbit": properties.get("orbit"),
							"frame_number": properties.get("frameNumber"),
							"flight_direction": properties.get("flightDirection"),
							"start_time": properties.get("startTime"),
							"stop_time": properties.get("stopTime"),
							"query_name": task.query_name,
							"feature_index": task.feature_index,
							"platform_filter": task.platform,
							"relative_orbit": task.relative_orbit,
						}
					)
			except Exception as error:
				scene_name = properties.get("sceneName") or properties.get("granuleName") or properties.get("fileName") or "unknown_scene"
				print(f"[SENTINEL-1] cena ignorada {scene_name}: {error}", flush=True)
				continue

	rows: list[dict[str, Any]] = []
	geometries = []
	for entry in orbit_map.values():
		footprints = [load_wkb(bytes.fromhex(geometry_wkb)) for geometry_wkb in entry["unique_footprints"]]
		merged_geometry = unary_union(footprints)
		geometries.append(merged_geometry)
		rows.append(
			{
				"orbit_key": entry["orbit_key"],
				"dataset": entry["dataset"],
				"platform": entry["platform"],
				"processing_level": entry["processing_level"],
				"flight_direction": entry["flight_direction"],
				"path_number": entry["path_number"],
				"frame_count": len({value for value in entry["frame_numbers"] if value not in (None, "")}),
				"frame_numbers": format_frame_numbers(entry["frame_numbers"]),
				"beam_mode": entry["beam_mode"],
				"scene_count": entry["scene_count"],
				"unique_footprints": len(entry["unique_footprints"]),
				"first_start_time": entry["first_start_time"],
				"last_stop_time": entry["last_stop_time"],
				"example_scene": entry["example_scene"],
			}
		)

	gdf = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")
	if gdf.empty:
		return gdf, scene_rows
	gdf = gdf.sort_values(["platform", "path_number", "flight_direction"], na_position="last").reset_index(drop=True)
	return gdf, scene_rows


def export_outputs(gdf: gpd.GeoDataFrame, scene_rows: list[dict[str, Any]]) -> None:
	output_dir = Path(OUTPUT_DIR)
	output_dir.mkdir(parents=True, exist_ok=True)
	combined_geojson_path = output_dir / "sentinel-1_unique_orbit_geometries_nov_2025_to_mar_2026.geojson"
	combined_csv_path = output_dir / "sentinel-1_unique_orbit_geometries.csv"
	if gdf.empty:
		write_csv(
			combined_csv_path,
			[],
			[
				"orbit_key",
				"dataset",
				"platform",
				"processing_level",
				"flight_direction",
				"path_number",
				"frame_count",
				"frame_numbers",
				"beam_mode",
				"scene_count",
				"unique_footprints",
				"first_start_time",
				"last_stop_time",
				"example_scene",
			],
		)
		print("Nenhuma orbita unica Sentinel-1 foi encontrada para os filtros configurados.")
		print(f"CSV vazio salvo em: {combined_csv_path}")
		return

	gdf.to_file(combined_geojson_path, driver="GeoJSON")
	write_csv(
		combined_csv_path,
		gdf.drop(columns="geometry").to_dict("records"),
		[
			"orbit_key",
			"dataset",
			"platform",
			"processing_level",
			"flight_direction",
			"path_number",
			"frame_count",
			"frame_numbers",
			"beam_mode",
			"scene_count",
			"unique_footprints",
			"first_start_time",
			"last_stop_time",
			"example_scene",
		],
	)

	if WRITE_SCENE_MANIFEST and scene_rows:
		write_csv(
			output_dir / "sentinel-1_scene_to_orbit_manifest.csv",
			scene_rows,
			[
				"orbit_key",
				"dataset",
				"platform",
				"scene_name",
				"file_name",
				"path_number",
				"absolute_orbit",
				"frame_number",
				"flight_direction",
				"start_time",
				"stop_time",
				"query_name",
				"feature_index",
				"platform_filter",
				"relative_orbit",
			],
		)

	print(f"GeoJSON Sentinel-1 salvo em: {combined_geojson_path}")
	print(f"CSV Sentinel-1 salvo em: {combined_csv_path}")
	print(f"Orbitas unicas Sentinel-1 exportadas: {len(gdf)}")


def main() -> None:
	validate_configuration()
	gdf, scene_rows = collect_unique_orbit_geometries()
	export_outputs(gdf, scene_rows)


if __name__ == "__main__":
	main()