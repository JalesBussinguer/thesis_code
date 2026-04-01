"""Consolida os KMLs de datasets/biomass_orbits em um unico GeoJSON.

Uso padrao:
    python utils/combine_biomass_orbits_geojson.py
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.ops import unary_union
from shapely.validation import make_valid


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = ROOT_DIR / "datasets" / "biomass_orbits"
DEFAULT_OUTPUT_PATH = DEFAULT_INPUT_DIR / "biomass_orbits.geojson"

def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Consolida todos os KMLs de biomass_orbits em um unico GeoJSON."
	)
	parser.add_argument(
		"--input-dir",
		type=Path,
		default=DEFAULT_INPUT_DIR,
		help="Diretorio com os arquivos .kml.",
	)
	parser.add_argument(
		"--output",
		type=Path,
		default=DEFAULT_OUTPUT_PATH,
		help="Caminho do GeoJSON de saida.",
	)
	return parser.parse_args()


def normalize_field_name(value: str) -> str:
	normalized = re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower()).strip("_")
	return normalized or "field"


def parse_coordinates(text: str) -> list[tuple[float, float]]:
	coordinates: list[tuple[float, float]] = []
	for raw_pair in text.split():
		parts = raw_pair.split(",")
		if len(parts) < 2:
			continue
		coordinates.append((float(parts[0]), float(parts[1])))
	if len(coordinates) < 4:
		raise ValueError("Coordenadas insuficientes para formar um poligono.")
	if coordinates[0] != coordinates[-1]:
		coordinates.append(coordinates[0])
	return coordinates


def strip_namespaces(root: ET.Element) -> ET.Element:
	for element in root.iter():
		if not isinstance(element.tag, str):
			continue
		element.tag = element.tag.split("}", 1)[-1]
	return root


def extract_extended_data(placemark: ET.Element) -> dict[str, str]:
	properties: dict[str, str] = {}
	for data_node in placemark.findall(".//ExtendedData/Data"):
		field_name = normalize_field_name(data_node.attrib.get("name", "field"))
		value_node = data_node.find("value")
		properties[field_name] = (value_node.text or "").strip() if value_node is not None else ""
	return properties


def extract_polygons(root: ET.Element) -> tuple[list[Polygon], dict[str, str]]:
	polygons: list[Polygon] = []
	properties: dict[str, str] = {}

	for placemark in root.findall(".//Placemark"):
		if not properties:
			properties = extract_extended_data(placemark)

		for coordinates_node in placemark.findall(
			".//Polygon/outerBoundaryIs/LinearRing/coordinates",
		):
			if not coordinates_node.text:
				continue
			polygon = Polygon(parse_coordinates(coordinates_node.text.strip()))
			if not polygon.is_empty:
				polygons.append(polygon)

	if polygons:
		return polygons, properties

	for coordinates_node in root.findall(".//LatLonQuad/coordinates"):
		if not coordinates_node.text:
			continue
		polygon = Polygon(parse_coordinates(coordinates_node.text.strip()))
		if not polygon.is_empty:
			polygons.append(polygon)

	return polygons, properties


def build_feature(kml_path: Path) -> dict[str, object]:
	root = strip_namespaces(ET.fromstring(kml_path.read_text(encoding="utf-8")))
	polygons, properties = extract_polygons(root)
	if not polygons:
		raise ValueError(f"Nenhum poligono encontrado em {kml_path.name}.")

	geometry = make_valid(unary_union(polygons))
	if isinstance(geometry, Polygon):
		final_geometry = geometry
	elif isinstance(geometry, MultiPolygon):
		final_geometry = geometry
	else:
		polygon_parts = [item for item in getattr(geometry, "geoms", []) if isinstance(item, Polygon)]
		if not polygon_parts:
			raise ValueError(f"Geometria invalida apos uniao em {kml_path.name}.")
		final_geometry = MultiPolygon(polygon_parts) if len(polygon_parts) > 1 else polygon_parts[0]

	properties = {
		"file_name": kml_path.stem,
		"source_file": kml_path.name,
		**properties,
	}
	return {
		"type": "Feature",
		"properties": properties,
		"geometry": mapping(final_geometry),
	}


def combine_kmls(input_dir: Path) -> dict[str, object]:
	if not input_dir.exists():
		raise FileNotFoundError(f"Diretorio nao encontrado: {input_dir}")

	features = [build_feature(kml_path) for kml_path in sorted(input_dir.glob("*.kml"))]
	if not features:
		raise FileNotFoundError(f"Nenhum arquivo .kml encontrado em: {input_dir}")

	return {
		"type": "FeatureCollection",
		"name": "biomass_orbits",
		"features": features,
	}


def write_geojson(feature_collection: dict[str, object], output_path: Path) -> None:
	output_path.parent.mkdir(parents=True, exist_ok=True)
	output_path.write_text(
		json.dumps(feature_collection, ensure_ascii=False, indent=2),
		encoding="utf-8",
	)


def main() -> int:
	args = parse_args()
	input_dir = args.input_dir.resolve()
	output_path = args.output.resolve()

	feature_collection = combine_kmls(input_dir)
	write_geojson(feature_collection, output_path)

	print(f"KMLs lidos em: {input_dir}")
	print(f"Feicoes geradas: {len(feature_collection['features'])}")
	print(f"GeoJSON salvo em: {output_path}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())