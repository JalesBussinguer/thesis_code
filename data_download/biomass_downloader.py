"""Download de produtos BIOMASS do ESA MAAP a partir de poligonos em GeoJSON.

Edite os parametros na secao CONFIGURACAO e execute o arquivo diretamente no VS Code.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urljoin

import geopandas as gpd
import requests
from pystac import Item
from pystac_client import Client
from shapely.geometry import mapping
from shapely.geometry import shape
from shapely.ops import unary_union
from tqdm import tqdm

CATALOG_URL = "https://catalog.maap.eo.esa.int/catalogue/"
TOKEN_URL = "https://iam.maap.eo.esa.int/realms/esa-maap/protocol/openid-connect/token"
CLIENT_ID = "offline-token"
CLIENT_SECRET = "p1eL7uonXs6MDxtGbgKdPVRAmnGxHpVE"
DEFAULT_COLLECTION = "BiomassLevel1a"
DEFAULT_DATETIME = "../.."
DEFAULT_TIMEOUT = 120
DEFAULT_ASSETS = ["product"]
DEFAULT_PRODUCT_TYPE_BY_COLLECTION = {
	"BiomassLevel1a": "S1_SCS__1S",
	"BiomassLevel1b": "S1_DGM__1S",
}


# =========================
# CONFIGURACAO DO USUARIO
# =========================
SEARCH_GEOJSON_PATH = "datasets/cerrado_bbox.geojson"
VALIDATION_GEOJSON_PATH = "datasets/cerrado_border.geojson"
OUTPUT_DIR = "H:/biomass_data/"
COLLECTION = DEFAULT_COLLECTION
DATETIME_RANGE = "2025-11-20T00:00:00Z/2026-06-12T23:59:59Z"
ASSET_KEYS = ["product"]
PRODUCT_TYPE = DEFAULT_PRODUCT_TYPE_BY_COLLECTION.get(COLLECTION)
ADDITIONAL_FILTER = None
MAX_ITEMS = None
PROPERTY_FIELD = None
REQUEST_TIMEOUT = DEFAULT_TIMEOUT
SKIP_EXISTING = True
MIN_DOWNLOAD_SIZE_MB = 100

# Se definido, baixa apenas os itens listados (um item ID/title por linha)
# e ignora a busca por poligono/data.
PRODUCT_LIST_TXT: str | None = None

# Informe apenas um deles.
ACCESS_TOKEN = None
OFFLINE_TOKEN = None
CREDENTIALS_FILE = Path("credentials.txt")


def validate_configuration() -> None:
	if not SEARCH_GEOJSON_PATH:
		raise ValueError("Defina SEARCH_GEOJSON_PATH com o caminho do GeoJSON de busca.")
	if not OUTPUT_DIR:
		raise ValueError("Defina OUTPUT_DIR com o diretorio de saida.")
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
	"""Read key-value pairs from a credentials file into a dictionary."""
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


def read_polygons(geojson_path: str, property_field: str | None) -> list[dict]:
	gdf = gpd.read_file(geojson_path)
	if gdf.empty:
		raise ValueError("O GeoJSON nao contem feicoes.")

	if gdf.crs is None:
		raise ValueError("O GeoJSON precisa ter CRS definido.")

	gdf = gdf.to_crs("EPSG:4326")
	geometries = gdf.geometry
	valid_mask = geometries.notnull() & ~geometries.is_empty
	gdf = gdf.loc[valid_mask].copy()
	if gdf.empty:
		raise ValueError("Nenhuma geometria valida foi encontrada no GeoJSON.")

	gdf["geometry"] = gdf.geometry.make_valid()
	polygon_rows: list[dict] = []

	for idx, row in gdf.iterrows():
		geom = row.geometry
		if geom.geom_type not in {"Polygon", "MultiPolygon"}:
			continue

		polygon_name = None
		if property_field and property_field in row and row[property_field] not in (None, ""):
			polygon_name = str(row[property_field])
		if not polygon_name:
			polygon_name = f"polygon_{idx}"

		polygon_rows.append(
			{
				"feature_index": idx,
				"polygon_name": polygon_name,
				"geometry": mapping(geom),
			}
		)

	if not polygon_rows:
		raise ValueError("Nenhum Polygon ou MultiPolygon foi encontrado no GeoJSON.")

	return polygon_rows


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


def item_intersects_validation_aoi(item: Item, validation_geometry) -> bool:
	if validation_geometry is None:
		return True
	if item.geometry is None:
		return False
	geometry = shape(item.geometry)
	if geometry.is_empty:
		return False
	return bool(geometry.intersects(validation_geometry))


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
	geometry: dict,
	datetime_range: str,
	cql2_filter: str | None,
	max_items: int | None,
) -> Iterator[Item]:
	intersects_geometry: Any = geometry
	if isinstance(intersects_geometry, dict) and intersects_geometry.get("type") == "Feature":
		intersects_geometry = intersects_geometry.get("geometry")

	if not isinstance(intersects_geometry, dict):
		raise ValueError("A geometria de busca precisa ser um objeto GeoJSON de geometria.")

	intersects_geometry = json.loads(json.dumps(intersects_geometry))
	search_link = catalog.get_search_link()
	if search_link is None or not search_link.target:
		raise RuntimeError("O catalogo STAC nao exibe link de busca (/search).")

	search_url = str(search_link.target)
	payload: dict[str, Any] = {
		"collections": [collection],
		"intersects": intersects_geometry,
		"datetime": datetime_range,
	}
	if cql2_filter:
		payload["filter"] = cql2_filter
		payload["filter-lang"] = "cql2-text"
	if max_items is not None:
		payload["limit"] = max_items

	remaining = max_items
	request_method = "POST"
	request_url = search_url
	request_body: dict[str, Any] | None = payload
	visited_pages: set[tuple[str, str]] = set()

	while request_url:
		request_key = (request_method.upper(), request_url)
		if request_key in visited_pages:
			break
		visited_pages.add(request_key)

		request_kwargs: dict[str, Any] = {"timeout": REQUEST_TIMEOUT}
		if request_method.upper() == "POST":
			request_kwargs["json"] = request_body or {}
		response = requests.request(request_method.upper(), request_url, **request_kwargs)

		if response.status_code >= 400:
			body = response.text.strip()
			if len(body) > 1200:
				body = body[:1200] + "..."
			raise RuntimeError(
				"Erro na consulta STAC ao iterar resultados com intersects. "
				f"collection={collection}; datetime={datetime_range}; status={response.status_code}; detalhes={body}"
			)

		result = response.json()
		for feature in result.get("features", []):
			yield Item.from_dict(feature)
			if remaining is not None:
				remaining -= 1
				if remaining <= 0:
					return

		next_link = None
		for link in result.get("links", []):
			if isinstance(link, dict) and link.get("rel") == "next":
				next_link = link
				break
		if not next_link:
			break

		next_href = next_link.get("href")
		if not isinstance(next_href, str) or not next_href:
			break

		request_url = urljoin(search_url, next_href)
		request_method = str(next_link.get("method", "GET")).upper()
		request_body = next_link.get("body") if isinstance(next_link.get("body"), dict) else None


def item_product_type(item: Item) -> str | None:
	value = item.properties.get("product:type")
	if isinstance(value, str):
		return value
	return None


def sanitize_name(value: str) -> str:
	cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
	return cleaned.strip("._") or "unnamed"


def resolve_filename(url: str, asset_key: str) -> str:
	candidate = url.rstrip("/").rsplit("/", 1)[-1]
	if "." in candidate:
		return candidate
	return f"{candidate or asset_key}.zip"


def format_size_mb(size_bytes: int) -> str:
	return f"{size_bytes / (1024 * 1024):.1f} MB"


def download_asset(
	url: str,
	destination: Path,
	access_token: str,
	timeout: int,
	skip_existing: bool,
) -> str:
	destination.parent.mkdir(parents=True, exist_ok=True)
	if destination.exists():
		return "skipped"

	with requests.get(
		url,
		headers={"Authorization": f"Bearer {access_token}"},
		stream=True,
		timeout=timeout,
	) as response:
		response.raise_for_status()
		total = int(response.headers.get("content-length", 0))
		if 0 < total < MIN_DOWNLOAD_SIZE_MB * 1024 * 1024:
			print(
				f"Ignorando {destination.name}: {format_size_mb(total)} abaixo do minimo de {MIN_DOWNLOAD_SIZE_MB} MB."
			)
			return "below_min_size"
		temp_path = destination.with_suffix(destination.suffix + ".part")
		with temp_path.open("wb") as file_handle, tqdm(
			total=total,
			unit="B",
			unit_scale=True,
			desc=destination.name,
		) as progress:
			for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
				if not chunk:
					continue
				bytes_written = file_handle.write(chunk)
				progress.update(bytes_written)
		temp_path.replace(destination)

	return "downloaded"


def asset_href(item: Item, asset_key: str) -> str | None:
	asset = item.assets.get(asset_key)
	if asset is None:
		return None
	return asset.href


def read_product_list(path: str | None) -> list[str]:
	if not path:
		return []
	resolved = Path(path)
	if not resolved.exists():
		raise FileNotFoundError(f"Lista de produtos nao encontrada: {resolved}")
	with resolved.open("r", encoding="utf-8") as fh:
		return [line.strip() for line in fh if line.strip() and not line.strip().startswith("#")]


def search_items_by_ids(catalog: Client, collection: str, item_ids: list[str]) -> list[Item]:
	search_link = catalog.get_search_link()
	if search_link is None or not search_link.target:
		raise RuntimeError("O catalogo STAC nao exibe link de busca (/search).")
	response = requests.post(
		str(search_link.target),
		json={"collections": [collection], "ids": item_ids},
		timeout=REQUEST_TIMEOUT,
	)
	response.raise_for_status()
	return [Item.from_dict(feature) for feature in response.json().get("features", [])]


def write_manifest(output_dir: Path, rows: Iterable[dict]) -> Path:
	manifest_path = output_dir / "download_manifest.csv"
	rows = list(rows)
	fieldnames = [
		"polygon_name",
		"feature_index",
		"item_id",
		"collection",
		"datetime",
		"asset_key",
		"asset_url",
		"download_path",
		"status",
	]
	with manifest_path.open("w", newline="", encoding="utf-8") as csvfile:
		writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)
	return manifest_path


def download_items(output_dir: Path, access_token: str, items: list[Item], effective_product_type: str | None, polygon_name: str, feature_index: Any) -> tuple[list[dict], set[tuple[str, str]]]:
	rows: list[dict] = []
	seen_downloads: set[tuple[str, str]] = set()
	for item in items:
		item_datetime = item.datetime.isoformat() if item.datetime else ""
		for asset_key in ASSET_KEYS:
			url = asset_href(item, asset_key)
			if not url:
				rows.append(
					{
						"polygon_name": polygon_name,
						"feature_index": feature_index,
						"item_id": item.id,
						"collection": item.collection_id or COLLECTION,
						"datetime": item_datetime,
						"asset_key": asset_key,
						"asset_url": "",
						"download_path": "",
						"status": "asset_not_found",
					}
				)
				continue

			filename = resolve_filename(url, asset_key)
			destination = output_dir / filename

			dedupe_key = (item.id, asset_key)
			if dedupe_key in seen_downloads and destination.exists():
				status = "already_downloaded"
			else:
				status = download_asset(
					url=url,
					destination=destination,
					access_token=access_token,
					timeout=REQUEST_TIMEOUT,
					skip_existing=SKIP_EXISTING,
				)
				seen_downloads.add(dedupe_key)

			rows.append(
				{
					"polygon_name": polygon_name,
					"feature_index": feature_index,
					"item_id": item.id,
					"collection": item.collection_id or COLLECTION,
					"datetime": item_datetime,
					"asset_key": asset_key,
					"asset_url": url,
					"download_path": str(destination.resolve()),
					"status": status,
				}
			)
	return rows, seen_downloads


def main() -> None:
	output_dir = Path(OUTPUT_DIR)
	output_dir.mkdir(parents=True, exist_ok=True)
	access_token = get_token()
	catalog = Client.open(CATALOG_URL)

	product_list = read_product_list(PRODUCT_LIST_TXT)
	if product_list:
		print(f"Modo lista: buscando {len(product_list)} item(ns) em {PRODUCT_LIST_TXT}...")
		items = search_items_by_ids(catalog, COLLECTION, product_list)
		print(f"  {len(items)} item(ns) encontrado(s).")
		manifest_rows, _ = download_items(output_dir, access_token, items, None, "product_list", "")
		manifest_path = write_manifest(output_dir, manifest_rows)
		summary = {
			"product_list_txt": str(Path(PRODUCT_LIST_TXT).resolve()),
			"output_dir": str(output_dir.resolve()),
			"collection": COLLECTION,
			"assets": ASSET_KEYS,
			"total_items_requested": len(product_list),
			"total_items_found": len(items),
			"manifest": str(manifest_path.resolve()),
		}
		print(json.dumps(summary, indent=2, ensure_ascii=True))
		return

	validate_configuration()
	polygons = read_polygons(SEARCH_GEOJSON_PATH, PROPERTY_FIELD)
	validation_geometry = read_validation_geometry(VALIDATION_GEOJSON_PATH)
	effective_product_type = PRODUCT_TYPE or DEFAULT_PRODUCT_TYPE_BY_COLLECTION.get(COLLECTION)
	cql2_filter = build_filter(effective_product_type, ADDITIONAL_FILTER)

	manifest_rows: list[dict] = []
	seen_downloads: set[tuple[str, str]] = set()
	total_items_found = 0

	for polygon in polygons:
		polygon_name = sanitize_name(polygon["polygon_name"])
		feature_index = polygon["feature_index"]
		print(
			f"Consultando poligono '{polygon_name}' (feature_index={feature_index}) na colecao {COLLECTION}..."
		)

		items = list(
			search_items(
				catalog=catalog,
				collection=COLLECTION,
				geometry=polygon["geometry"],
				datetime_range=DATETIME_RANGE,
				cql2_filter=cql2_filter,
				max_items=MAX_ITEMS,
			)
		)
		if effective_product_type:
			items = [item for item in items if item_product_type(item) == effective_product_type]
		total_items_found += len(items)
		print(f"  {len(items)} item(ns) encontrado(s).")

		for item in items:
			if not item_intersects_validation_aoi(item, validation_geometry):
				continue
			item_datetime = item.datetime.isoformat() if item.datetime else ""

			for asset_key in ASSET_KEYS:
				url = asset_href(item, asset_key)
				if not url:
					manifest_rows.append(
						{
							"polygon_name": polygon_name,
							"feature_index": feature_index,
							"item_id": item.id,
							"collection": item.collection_id or COLLECTION,
							"datetime": item_datetime,
							"asset_key": asset_key,
							"asset_url": "",
							"download_path": "",
							"status": "asset_not_found",
						}
					)
					continue

				filename = resolve_filename(url, asset_key)
				destination = output_dir / filename

				dedupe_key = (item.id, asset_key)
				if dedupe_key in seen_downloads and destination.exists():
					status = "already_downloaded"
				else:
					status = download_asset(
						url=url,
						destination=destination,
						access_token=access_token,
						timeout=REQUEST_TIMEOUT,
						skip_existing=SKIP_EXISTING,
					)
					seen_downloads.add(dedupe_key)

				manifest_rows.append(
					{
						"polygon_name": polygon_name,
						"feature_index": feature_index,
						"item_id": item.id,
						"collection": item.collection_id or COLLECTION,
						"datetime": item_datetime,
						"asset_key": asset_key,
						"asset_url": url,
						"download_path": str(destination.resolve()),
						"status": status,
					}
				)

	manifest_path = write_manifest(output_dir, manifest_rows)
	summary = {
		"search_geojson": str(Path(SEARCH_GEOJSON_PATH).resolve()),
		"validation_geojson": str(Path(VALIDATION_GEOJSON_PATH).resolve()),
		"output_dir": str(output_dir.resolve()),
		"collection": COLLECTION,
		"datetime": DATETIME_RANGE,
		"assets": ASSET_KEYS,
		"total_polygons": len(polygons),
		"total_items_found": total_items_found,
		"manifest": str(manifest_path.resolve()),
	}
	print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
	main()
