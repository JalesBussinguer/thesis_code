"""Baixa geometrias de orbitas BIOMASS a partir de um GeoJSON de referencia.

O script consulta o catalogo STAC do ESA MAAP, identifica orbitas unicas
intersectando o GeoJSON informado, baixa um arquivo KML por orbita e exporta
um manifesto CSV e um GeoJSON consolidado.

Uso padrao:
    python data_download/biomass_orbit_geometry_downloader.py
"""

from __future__ import annotations

import csv
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import geopandas as gpd
import requests
from pystac import Item
from pystac_client import Client
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

CATALOG_URL = "https://catalog.maap.eo.esa.int/catalogue/"
TOKEN_URL = "https://iam.maap.eo.esa.int/realms/esa-maap/protocol/openid-connect/token"
CLIENT_ID = "offline-token"
CLIENT_SECRET = "p1eL7uonXs6MDxtGbgKdPVRAmnGxHpVE"
DEFAULT_COLLECTION = "BiomassLevel1a"
DEFAULT_DATETIME = "2025-11-01T00:00:00Z/.."
DEFAULT_TIMEOUT = 120
DEFAULT_PRODUCT_TYPE_BY_COLLECTION = {
	"BiomassLevel1a": "S1_SCS__1S",
	"BiomassLevel1b": "S1_DGM__1S",
}

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "datasets" / "biomass_orbits"
DEFAULT_MANIFEST_PATH = DEFAULT_OUTPUT_DIR / "orbit_examples.csv"
DEFAULT_GEOJSON_PATH = DEFAULT_OUTPUT_DIR / "biomass_orbits.geojson"


# =========================
# CONFIGURACAO DO USUARIO
# =========================
SEARCH_GEOJSON_PATH = "datasets/cerrado_bbox.geojson"
VALIDATION_GEOJSON_PATH = "datasets/cerrado_border.geojson"
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
MANIFEST_PATH = DEFAULT_MANIFEST_PATH
COMBINED_GEOJSON_PATH = DEFAULT_GEOJSON_PATH
COLLECTION = "BiomassLevel1a"
DATETIME_RANGE = DEFAULT_DATETIME
PRODUCT_TYPE = DEFAULT_PRODUCT_TYPE_BY_COLLECTION.get(COLLECTION)
ADDITIONAL_FILTER = None
MAX_ITEMS = None
REQUEST_TIMEOUT = DEFAULT_TIMEOUT
SKIP_EXISTING = True
WRITE_COMBINED_GEOJSON = True

# Informe apenas um deles.
ACCESS_TOKEN = None
OFFLINE_TOKEN = None
CREDENTIALS_FILE = Path("credentials.txt")


@dataclass(frozen=True)
class SearchPolygon:
	feature_index: int
	polygon_name: str
	geometry: dict[str, object]


@dataclass(frozen=True)
class OrbitCandidate:
	orbit_key: str
	product_name: str
	kml_url: str
	start_datetime: str | None
	end_datetime: str | None
	track: str
	frame: str
	mode: str
	repeat_cycle: str | None
	orbit_state: str | None
	feature_index: int
	polygon_name: str


def validate_configuration() -> None:
	if not SEARCH_GEOJSON_PATH:
		raise ValueError("Defina SEARCH_GEOJSON_PATH com o caminho do GeoJSON de busca.")
	if not OUTPUT_DIR:
		raise ValueError("Defina OUTPUT_DIR com o diretorio de saida.")
	if not COLLECTION:
		raise ValueError("Defina COLLECTION com a colecao STAC do BIOMASS.")
	if not MANIFEST_PATH:
		raise ValueError("Defina MANIFEST_PATH para o CSV de saida.")
	if WRITE_COMBINED_GEOJSON and not COMBINED_GEOJSON_PATH:
		raise ValueError("Defina COMBINED_GEOJSON_PATH para o GeoJSON consolidado.")
	if (
		not ACCESS_TOKEN
		and not OFFLINE_TOKEN
		and not CREDENTIALS_FILE
		and not os.getenv("ESA_MAAP_OFFLINE_TOKEN")
	):
		raise ValueError(
			"Informe ACCESS_TOKEN, OFFLINE_TOKEN, CREDENTIALS_FILE ou a variavel ESA_MAAP_OFFLINE_TOKEN."
		)


def normalize_token(raw_value: str) -> str:
	token = raw_value.strip().replace("\ufeff", "")
	token = token.removeprefix("Bearer ").removeprefix("bearer ").strip()

	if token.startswith("{"):
		try:
			payload = json.loads(token)
			for key in ("refresh_token", "offline_token", "access_token", "token"):
				value = payload.get(key)
				if isinstance(value, str) and value.strip():
					return normalize_token(value)
		except json.JSONDecodeError:
			pass

	for separator in ("=", ":"):
		for key in ("refresh_token", "offline_token", "access_token", "token"):
			prefix = f"{key}{separator}"
			if token.lower().startswith(prefix):
				return normalize_token(token[len(prefix):])

	if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
		token = token[1:-1].strip()

	return token


def load_credentials(file_path: Path = CREDENTIALS_FILE) -> dict[str, str]:
	creds: dict[str, str] = {}
	if not file_path.exists():
		raise FileNotFoundError(f"Credentials file not found: {file_path.resolve()}")

	with file_path.open("r", encoding="utf-8") as file_handle:
		for line in file_handle:
			line = line.strip()
			if not line or line.startswith("#"):
				continue
			if "=" not in line:
				continue
			key, value = line.split("=", 1)
			creds[key.strip()] = value.strip()

	return creds


def build_token_error(response: requests.Response) -> RuntimeError:
	body = response.text.strip()
	if len(body) > 600:
		body = body[:600] + "..."

	message = (
		"Falha ao gerar access token no ESA MAAP. "
		f"Status HTTP: {response.status_code}. Resposta: {body}\n\n"
		"Causas provaveis:\n"
		"- o valor em credentials.txt/OFFLINE_TOKEN nao e um offline token valido\n"
		"- o offline token expirou ou foi revogado\n"
		"- o arquivo contem um access token em vez de um offline token\n"
		"- o token foi salvo com texto extra, JSON, aspas ou prefixos inesperados\n\n"
		"Se necessario, gere um novo offline token no portal MAAP e substitua o conteudo do arquivo."
	)
	return RuntimeError(message)


def get_token() -> str:
	if ACCESS_TOKEN:
		return normalize_token(ACCESS_TOKEN)

	if OFFLINE_TOKEN:
		offline_token = normalize_token(OFFLINE_TOKEN)
		client_id = CLIENT_ID
		client_secret = CLIENT_SECRET
	else:
		env_token = os.getenv("ESA_MAAP_OFFLINE_TOKEN")
		if env_token:
			offline_token = normalize_token(env_token)
			client_id = CLIENT_ID
			client_secret = CLIENT_SECRET
		else:
			creds = load_credentials()
			offline_token = normalize_token(creds.get("OFFLINE_TOKEN", ""))
			client_id = creds.get("CLIENT_ID", CLIENT_ID).strip()
			client_secret = creds.get("CLIENT_SECRET", CLIENT_SECRET).strip()

	if not offline_token:
		raise ValueError(
			"Missing OFFLINE_TOKEN. Defina OFFLINE_TOKEN, ESA_MAAP_OFFLINE_TOKEN ou informe-o em credentials.txt."
		)

	if not all([client_id, client_secret]):
		raise ValueError(
			"Missing CLIENT_ID or CLIENT_SECRET. Informe-os em credentials.txt ou no script."
		)

	response = requests.post(
		TOKEN_URL,
		data={
			"client_id": client_id,
			"client_secret": client_secret,
			"grant_type": "refresh_token",
			"refresh_token": offline_token,
			"scope": "offline_access openid",
		},
		timeout=REQUEST_TIMEOUT,
	)
	if response.status_code >= 400:
		raise build_token_error(response)

	access_token = response.json().get("access_token")
	if not access_token:
		raise RuntimeError("A resposta do IAM nao contem access_token.")

	return access_token


def normalize_field_name(value: str) -> str:
	normalized = re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower()).strip("_")
	return normalized or "field"


def normalize_token_value(value: object) -> str | None:
	if value in (None, ""):
		return None
	if isinstance(value, list):
		return ",".join(str(item) for item in value)
	return str(value)


def read_polygons(geojson_path: str) -> list[SearchPolygon]:
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
	polygons: list[SearchPolygon] = []

	for idx, row in gdf.iterrows():
		geom = row.geometry
		parts = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
		for part_index, part in enumerate(parts, start=1):
			if part.geom_type != "Polygon":
				continue
			polygons.append(
				SearchPolygon(
					feature_index=idx,
					polygon_name=f"feature_{idx}_part_{part_index}",
					geometry=mapping(part),
				)
			)

	if not polygons:
		raise ValueError("Nenhum Polygon ou MultiPolygon foi encontrado no GeoJSON.")

	return polygons


def read_validation_geometry(geojson_path: str | None):
	if not geojson_path:
		return None
	gdf = gpd.read_file(geojson_path)
	if gdf.empty:
		raise ValueError("O GeoJSON de validacao nao contem feicoes.")
	if gdf.crs is None:
		raise ValueError("O GeoJSON de validacao precisa ter CRS definido.")
	gdf = gdf.to_crs("EPSG:4326")
	gdf = gdf.loc[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()
	if gdf.empty:
		raise ValueError("Nenhuma geometria valida foi encontrada no GeoJSON de validacao.")
	gdf["geometry"] = gdf.geometry.make_valid()
	return unary_union(list(gdf.geometry))


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


def build_orbit_key(product_name: str) -> tuple[str, str, str]:
	parts = split_product_name(product_name)
	if len(parts) <= 11:
		raise ValueError(
			"Nome de produto invalido para extrair a chave de orbita: "
			f"{product_name}"
		)
	return "_".join((parts[2], parts[10], parts[11])), parts[10], parts[11]


def get_kml_asset_href(item: Item) -> str:
	kml_asset = item.assets.get("enclosure_kml")
	if kml_asset and kml_asset.href:
		return kml_asset.href

	for asset in item.assets.values():
		if asset.href.lower().endswith(".kml"):
			return asset.href

	raise ValueError(f"Item sem asset KML: {item.id}")


def normalize_repeat_cycle(properties: dict[str, object]) -> str | None:
	return normalize_token_value(properties.get("eofeos:repeat_cycle_id"))


def normalize_orbit_state(properties: dict[str, object]) -> str | None:
	state = normalize_token_value(properties.get("sat:orbit_state"))
	return state.upper() if state else None


def is_candidate_newer(current: OrbitCandidate, incoming: OrbitCandidate) -> bool:
	current_time = current.start_datetime or ""
	incoming_time = incoming.start_datetime or ""
	if incoming_time != current_time:
		return incoming_time > current_time
	return incoming.product_name > current.product_name


def collect_unique_orbits() -> list[OrbitCandidate]:
	polygons = read_polygons(SEARCH_GEOJSON_PATH)
	validation_geometry = read_validation_geometry(VALIDATION_GEOJSON_PATH)
	catalog = Client.open(CATALOG_URL)
	cql2_filter = build_filter(PRODUCT_TYPE, ADDITIONAL_FILTER)
	orbits: dict[str, OrbitCandidate] = {}
	seen_items: set[str] = set()

	for polygon in polygons:
		items = search_items(
			catalog=catalog,
			collection=COLLECTION,
			geometry=polygon.geometry,
			datetime_range=DATETIME_RANGE,
			cql2_filter=cql2_filter,
			max_items=MAX_ITEMS,
		)

		for item in items:
			if item.id in seen_items:
				continue
			seen_items.add(item.id)
			if validation_geometry is not None:
				if item.geometry is None:
					continue
				item_geometry = make_valid(shape(item.geometry))
				if item_geometry.is_empty or not item_geometry.intersects(validation_geometry):
					continue

			product_name = normalize_token_value(item.properties.get("title")) or item.id
			orbit_key, track, frame = build_orbit_key(product_name)
			candidate = OrbitCandidate(
				orbit_key=orbit_key,
				product_name=product_name,
				kml_url=get_kml_asset_href(item),
				start_datetime=normalize_token_value(item.properties.get("start_datetime")),
				end_datetime=normalize_token_value(item.properties.get("end_datetime")),
				track=track,
				frame=frame,
				mode=split_product_name(product_name)[2],
				repeat_cycle=normalize_repeat_cycle(item.properties),
				orbit_state=normalize_orbit_state(item.properties),
				feature_index=polygon.feature_index,
				polygon_name=polygon.polygon_name,
			)

			current = orbits.get(orbit_key)
			if current is None or is_candidate_newer(current, candidate):
				orbits[orbit_key] = candidate

	return sorted(orbits.values(), key=lambda item: (item.track, item.frame, item.product_name))


def download_file(url: str, destination: Path, access_token: str) -> None:
	response = requests.get(
		url,
		headers={"Authorization": f"Bearer {access_token}"},
		timeout=REQUEST_TIMEOUT,
	)
	response.raise_for_status()
	destination.parent.mkdir(parents=True, exist_ok=True)
	destination.write_bytes(response.content)


def download_kmls(orbits: Iterable[OrbitCandidate], access_token: str) -> list[dict[str, str | None]]:
	output_dir = Path(OUTPUT_DIR)
	rows: list[dict[str, str | None]] = []

	for orbit in orbits:
		output_path = output_dir / f"{orbit.orbit_key}.kml"
		if not (SKIP_EXISTING and output_path.exists()):
			download_file(orbit.kml_url, output_path, access_token)

		rows.append(
			{
				"orbit_key": orbit.orbit_key,
				"track": orbit.track,
				"frame": orbit.frame,
				"mode": orbit.mode,
				"product_name": orbit.product_name,
				"start_datetime": orbit.start_datetime,
				"end_datetime": orbit.end_datetime,
				"repeat_cycle": orbit.repeat_cycle,
				"orbit_state": orbit.orbit_state,
				"feature_index": str(orbit.feature_index),
				"polygon_name": orbit.polygon_name,
				"kml_url": orbit.kml_url,
				"output_kml": str(output_path.resolve()),
			}
		)

	return rows


def write_csv(csv_path: Path, rows: Iterable[dict[str, str | None]], fieldnames: list[str]) -> None:
	csv_path.parent.mkdir(parents=True, exist_ok=True)
	with csv_path.open("w", encoding="utf-8", newline="") as csvfile:
		writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)


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


def write_combined_geojson(output_path: Path) -> int:
	input_dir = Path(OUTPUT_DIR)
	features = [build_feature(kml_path) for kml_path in sorted(input_dir.glob("*.kml"))]
	if not features:
		raise FileNotFoundError(f"Nenhum arquivo .kml encontrado em: {input_dir}")

	feature_collection = {
		"type": "FeatureCollection",
		"name": "biomass_orbits",
		"features": features,
	}
	output_path.parent.mkdir(parents=True, exist_ok=True)
	output_path.write_text(
		json.dumps(feature_collection, ensure_ascii=False, indent=2),
		encoding="utf-8",
	)
	return len(features)


def main() -> int:
	validate_configuration()
	access_token = get_token()
	orbits = collect_unique_orbits()
	manifest_rows = download_kmls(orbits, access_token)
	write_csv(
		Path(MANIFEST_PATH),
		manifest_rows,
		[
			"orbit_key",
			"track",
			"frame",
			"mode",
			"product_name",
			"start_datetime",
			"end_datetime",
			"repeat_cycle",
			"orbit_state",
			"feature_index",
			"polygon_name",
			"kml_url",
			"output_kml",
		],
	)

	print(f"Orbitas unicas selecionadas: {len(manifest_rows)}")
	print(f"KMLs salvos em: {Path(OUTPUT_DIR).resolve()}")
	print(f"Manifest salvo em: {Path(MANIFEST_PATH).resolve()}")

	if WRITE_COMBINED_GEOJSON:
		feature_count = write_combined_geojson(Path(COMBINED_GEOJSON_PATH))
		print(f"GeoJSON consolidado salvo em: {Path(COMBINED_GEOJSON_PATH).resolve()}")
		print(f"Feicoes consolidadas: {feature_count}")

	return 0


if __name__ == "__main__":
	raise SystemExit(main())