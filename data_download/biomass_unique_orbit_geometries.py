"""Gera geometrias de orbitas unicas do BIOMASS a partir do catalogo STAC.

O script consulta itens BIOMASS Level-1A no catalogo ESA MAAP, agrupa as
geometrias por orbita unica e exporta os resultados em GeoJSON e CSV.

Uso padrao:
    python data_download/biomass_unique_orbit_geometries.py
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Iterator

import geopandas as gpd
from pystac import Item
from pystac_client import Client
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.validation import make_valid
from shapely.wkb import loads as load_wkb

CATALOG_URL = "https://catalog.maap.eo.esa.int/catalogue/"
DEFAULT_COLLECTION = "BiomassLevel1a"
DEFAULT_DATETIME = "2025-11-01T00:00:00Z/.."
DEFAULT_PRODUCT_TYPE_BY_COLLECTION = {
	"BiomassLevel1a": "S1_SCS__1S",
	"BiomassLevel1b": "S1_DGM__1S",
}

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "datasets" / "biomass_orbits_unique"


# =========================
# CONFIGURACAO DO USUARIO
# =========================
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
GEOJSON_PATH = "datasets/cerrado_bbox.geojson"
COLLECTION = DEFAULT_COLLECTION
DATETIME_RANGE = DEFAULT_DATETIME
PRODUCT_TYPE = DEFAULT_PRODUCT_TYPE_BY_COLLECTION.get(COLLECTION)
ADDITIONAL_FILTER = None
MAX_ITEMS = None
WRITE_SCENE_MANIFEST = False


def validate_configuration() -> None:
	if not GEOJSON_PATH:
		raise ValueError("Defina GEOJSON_PATH com o caminho do GeoJSON de referencia.")
	if not OUTPUT_DIR:
		raise ValueError("Defina OUTPUT_DIR com o diretorio de saida.")
	if not COLLECTION:
		raise ValueError("Defina COLLECTION com a colecao STAC do BIOMASS.")


def normalize_text(value: Any) -> str | None:
	if value in (None, ""):
		return None
	if isinstance(value, list):
		return ",".join(str(item) for item in value)
	return str(value)


def read_search_geometries(geojson_path: str) -> list[dict[str, object]]:
	gdf = gpd.read_file(geojson_path)
	if gdf.empty:
		raise ValueError("O GeoJSON nao contem feicoes.")
	if gdf.crs is None:
		raise ValueError("O GeoJSON precisa ter CRS definido.")

	gdf = gdf.to_crs("EPSG:4326")
	gdf = gdf.loc[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()
	if gdf.empty:
		raise ValueError("Nenhuma geometria valida foi encontrada no GeoJSON.")

	gdf["geometry"] = gdf.geometry.make_valid()
	search_geometries: list[dict[str, object]] = []
	for idx, row in gdf.iterrows():
		geometry = row.geometry
		parts = [geometry] if geometry.geom_type == "Polygon" else list(getattr(geometry, "geoms", []))
		for part_index, part in enumerate(parts, start=1):
			if part.geom_type != "Polygon":
				continue
			search_geometries.append(
				{
					"feature_index": idx,
					"query_name": f"feature_{idx}_part_{part_index}",
					"geometry": part.__geo_interface__,
				}
			)

	if not search_geometries:
		raise ValueError("Nenhum Polygon ou MultiPolygon foi encontrado no GeoJSON.")

	return search_geometries


def build_filter(product_type: str | None, extra_filter: str | None) -> str | None:
	clauses: list[str] = []
	if product_type:
		clauses.append(f"product:type='{product_type}'")
	if extra_filter:
		clauses.append(extra_filter)
	if not clauses:
		return None
	return " and ".join(f"({clause})" for clause in clauses)


def search_items(
	catalog: Client,
	collection: str,
	geometry: dict[str, object],
	datetime_range: str,
	cql2_filter: str | None,
	max_items: int | None,
) -> Iterator[Item]:
	search = catalog.search(
		collections=[collection],
		intersects=geometry,
		datetime=datetime_range,
		filter=cql2_filter,
		method="POST",
		max_items=max_items,
	)
	yield from search.items()


def split_product_name(product_name: str) -> list[str]:
	return [part for part in product_name.split("_") if part]


def parse_orbit_fields(product_name: str) -> dict[str, Any]:
	parts = split_product_name(product_name)
	if len(parts) <= 11:
		raise ValueError(
			"Nome de produto invalido para extrair track/frame: "
			f"{product_name}"
		)

	mode = parts[2]
	track = parts[10]
	frame = parts[11]
	track_number = int(track.removeprefix("T")) if track.startswith("T") and track[1:].isdigit() else None
	frame_number = int(frame.removeprefix("F")) if frame.startswith("F") and frame[1:].isdigit() else None

	return {
		"orbit_key": "_".join((mode, track, frame)),
		"mode": mode,
		"track": track,
		"frame": frame,
		"track_number": track_number,
		"frame_number": frame_number,
	}


def write_csv(csv_path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
	csv_path.parent.mkdir(parents=True, exist_ok=True)
	with csv_path.open("w", encoding="utf-8", newline="") as csvfile:
		writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)


def collect_unique_orbit_geometries() -> tuple[gpd.GeoDataFrame, list[dict[str, Any]]]:
	search_geometries = read_search_geometries(GEOJSON_PATH)
	catalog = Client.open(CATALOG_URL)
	cql2_filter = build_filter(PRODUCT_TYPE, ADDITIONAL_FILTER)
	orbit_map: dict[str, dict[str, Any]] = {}
	scene_rows: list[dict[str, Any]] = []
	seen_items: set[str] = set()

	for search_geometry in search_geometries:
		items = search_items(
			catalog=catalog,
			collection=COLLECTION,
			geometry=search_geometry["geometry"],
			datetime_range=DATETIME_RANGE,
			cql2_filter=cql2_filter,
			max_items=MAX_ITEMS,
		)

		for item in items:
			if item.id in seen_items:
				continue
			seen_items.add(item.id)

			product_name = normalize_text(item.properties.get("title")) or item.id
			orbit_fields = parse_orbit_fields(product_name)
			if item.geometry is None:
				continue

			geometry = make_valid(shape(item.geometry))
			if geometry.is_empty:
				continue

			geometry_wkb = geometry.wkb_hex
			orbit_key = orbit_fields["orbit_key"]
			start_time = normalize_text(item.properties.get("start_datetime") or item.properties.get("datetime"))
			end_time = normalize_text(item.properties.get("end_datetime") or item.properties.get("datetime"))
			orbit_state = normalize_text(item.properties.get("sat:orbit_state"))
			repeat_cycle = normalize_text(item.properties.get("eofeos:repeat_cycle_id"))
			absolute_orbit = item.properties.get("sat:absolute_orbit")
			entry = orbit_map.setdefault(
				orbit_key,
				{
					"orbit_key": orbit_key,
					"dataset": "BIOMASS",
					"collection": COLLECTION,
					"product_type": PRODUCT_TYPE,
					"mode": orbit_fields["mode"],
					"orbit_state": orbit_state.upper() if orbit_state else None,
					"track": orbit_fields["track"],
					"track_number": orbit_fields["track_number"],
					"frame": orbit_fields["frame"],
					"frame_number": orbit_fields["frame_number"],
					"scene_count": 0,
					"unique_footprints": set(),
					"repeat_cycles": set(),
					"absolute_orbits": set(),
					"first_start_time": None,
					"last_stop_time": None,
					"example_scene": product_name,
				},
			)

			entry["scene_count"] += 1
			entry["unique_footprints"].add(geometry_wkb)
			if repeat_cycle:
				entry["repeat_cycles"].add(repeat_cycle)
			if absolute_orbit is not None:
				entry["absolute_orbits"].add(str(absolute_orbit))

			if start_time and (entry["first_start_time"] is None or start_time < entry["first_start_time"]):
				entry["first_start_time"] = start_time
			if end_time and (entry["last_stop_time"] is None or end_time > entry["last_stop_time"]):
				entry["last_stop_time"] = end_time

			if WRITE_SCENE_MANIFEST:
				scene_rows.append(
					{
						"orbit_key": orbit_key,
						"scene_name": product_name,
						"item_id": item.id,
						"collection": COLLECTION,
						"product_type": PRODUCT_TYPE,
						"mode": orbit_fields["mode"],
						"track": orbit_fields["track"],
						"frame": orbit_fields["frame"],
						"orbit_state": orbit_state.upper() if orbit_state else None,
						"repeat_cycle": repeat_cycle,
						"absolute_orbit": absolute_orbit,
						"start_time": start_time,
						"end_time": end_time,
						"query_name": search_geometry["query_name"],
						"feature_index": search_geometry["feature_index"],
					}
				)

	rows: list[dict[str, Any]] = []
	geometries = []
	for entry in orbit_map.values():
		footprint_geometries = []
		for geometry_wkb in entry["unique_footprints"]:
			footprint_geometries.append(load_wkb(bytes.fromhex(geometry_wkb)))
		merged_geometry = unary_union(footprint_geometries)
		geometries.append(merged_geometry)
		rows.append(
			{
				"orbit_key": entry["orbit_key"],
				"dataset": entry["dataset"],
				"collection": entry["collection"],
				"product_type": entry["product_type"],
				"mode": entry["mode"],
				"orbit_state": entry["orbit_state"],
				"track": entry["track"],
				"track_number": entry["track_number"],
				"frame": entry["frame"],
				"frame_number": entry["frame_number"],
				"scene_count": entry["scene_count"],
				"unique_footprints": len(entry["unique_footprints"]),
				"repeat_cycles": ",".join(sorted(entry["repeat_cycles"])),
				"absolute_orbits": ",".join(sorted(entry["absolute_orbits"])),
				"first_start_time": entry["first_start_time"],
				"last_stop_time": entry["last_stop_time"],
				"example_scene": entry["example_scene"],
			}
		)

	gdf = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")
	if gdf.empty:
		return gdf, scene_rows
	gdf = gdf.sort_values(["track_number", "frame_number"], na_position="last").reset_index(drop=True)
	return gdf, scene_rows


def export_outputs(gdf: gpd.GeoDataFrame, scene_rows: list[dict[str, Any]]) -> None:
	output_dir = Path(OUTPUT_DIR)
	output_dir.mkdir(parents=True, exist_ok=True)
	combined_geojson_path = output_dir / "unique_orbit_path_geometries.geojson"
	combined_csv_path = output_dir / "unique_orbit_path_geometries.csv"
	if gdf.empty:
		write_csv(
			combined_csv_path,
			[],
			[
				"orbit_key",
				"dataset",
				"collection",
				"product_type",
				"mode",
				"orbit_state",
				"track",
				"track_number",
				"frame",
				"frame_number",
				"scene_count",
				"unique_footprints",
				"repeat_cycles",
				"absolute_orbits",
				"first_start_time",
				"last_stop_time",
				"example_scene",
			],
		)
		print("Nenhuma orbita unica BIOMASS foi encontrada para os filtros configurados.")
		print(f"CSV vazio salvo em: {combined_csv_path}")
		return

	gdf.to_file(combined_geojson_path, driver="GeoJSON")
	write_csv(
		combined_csv_path,
		gdf.drop(columns="geometry").to_dict("records"),
		[
			"orbit_key",
			"dataset",
			"collection",
			"product_type",
			"mode",
			"orbit_state",
			"track",
			"track_number",
			"frame",
			"frame_number",
			"scene_count",
			"unique_footprints",
			"repeat_cycles",
			"absolute_orbits",
			"first_start_time",
			"last_stop_time",
			"example_scene",
		],
	)

	if WRITE_SCENE_MANIFEST and scene_rows:
		write_csv(
			output_dir / "scene_to_orbit_path_manifest.csv",
			scene_rows,
			[
				"orbit_key",
				"scene_name",
				"item_id",
				"collection",
				"product_type",
				"mode",
				"track",
				"frame",
				"orbit_state",
				"repeat_cycle",
				"absolute_orbit",
				"start_time",
				"end_time",
				"query_name",
				"feature_index",
			],
		)

	print(f"GeoJSON BIOMASS salvo em: {combined_geojson_path}")
	print(f"CSV BIOMASS salvo em: {combined_csv_path}")
	print(f"Orbitas unicas BIOMASS exportadas: {len(gdf)}")


def main() -> None:
	validate_configuration()
	gdf, scene_rows = collect_unique_orbit_geometries()
	export_outputs(gdf, scene_rows)


if __name__ == "__main__":
	main()