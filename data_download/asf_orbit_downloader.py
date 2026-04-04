"""Download de produtos de orbita do ASF Vertex para Sentinel-1 e NISAR.

Edite os parametros na secao CONFIGURACAO e execute o arquivo diretamente no VS Code.

O fluxo usa a Search API do ASF para localizar produtos por orbita, data e/ou AOI,
gera um manifest CSV e baixa os arquivos principais autenticando com Earthdata.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import requests
from requests import Session
from shapely import wkt as shapely_wkt
from shapely.ops import unary_union
from tqdm import tqdm

SEARCH_URL = "https://api.daac.asf.alaska.edu/services/search/param"
DEFAULT_TIMEOUT = 120
DEFAULT_PROCESSING_LEVEL_BY_DATASET = {
	"SENTINEL-1": "SLC",
	"NISAR": "RSLC",
}


# =========================
# CONFIGURACAO DO USUARIO
# =========================
DATASETS = ["SENTINEL-1", "NISAR"]
OUTPUT_DIR = "H:/asf_orbit_data/"
SEARCH_GEOJSON_PATH = "datasets/cerrado_bbox.geojson"  # Use None para pesquisar apenas por orbita.
VALIDATION_GEOJSON_PATH = "datasets/cerrado_border.geojson"  # Use None para baixar tudo que vier da busca.
DATE_START = "2026-01-01T00:00:00Z"
DATE_END = "2026-03-31T23:59:59Z"
PROCESSING_LEVEL_BY_DATASET = {
	"SENTINEL-1": DEFAULT_PROCESSING_LEVEL_BY_DATASET["SENTINEL-1"],
	"NISAR": DEFAULT_PROCESSING_LEVEL_BY_DATASET["NISAR"],
}
RELATIVE_ORBIT = None
ABSOLUTE_ORBIT = None
FRAME = None
ASF_FRAME = None
FLIGHT_DIRECTION = None  # "ASCENDING" ou "DESCENDING"
MAX_RESULTS = None
SKIP_EXISTING = True
DOWNLOAD_PRIMARY = True
DOWNLOAD_NISAR_KML = False
REQUEST_TIMEOUT = DEFAULT_TIMEOUT

# Informe EARTHDATA_TOKEN ou EARTHDATA_USERNAME/EARTHDATA_PASSWORD.
EARTHDATA_TOKEN = None
EARTHDATA_USERNAME = None
EARTHDATA_PASSWORD = None
CREDENTIALS_FILE = Path("credentials.txt")


def validate_configuration() -> None:
	if not DATASETS:
		raise ValueError("Defina ao menos um dataset em DATASETS.")
	invalid_datasets = [dataset for dataset in DATASETS if dataset not in DEFAULT_PROCESSING_LEVEL_BY_DATASET]
	if invalid_datasets:
		raise ValueError(
			"DATASETS contem valor(es) invalido(s): "
			+ ", ".join(invalid_datasets)
			+ ". Use 'SENTINEL-1' e/ou 'NISAR'."
		)
	if not OUTPUT_DIR:
		raise ValueError("Defina OUTPUT_DIR com o diretorio de saida.")
	if not any((SEARCH_GEOJSON_PATH, RELATIVE_ORBIT, ABSOLUTE_ORBIT, FRAME, ASF_FRAME)):
		raise ValueError(
			"Informe ao menos um filtro espacial/de orbita: SEARCH_GEOJSON_PATH, RELATIVE_ORBIT, "
			"ABSOLUTE_ORBIT, FRAME ou ASF_FRAME."
		)
	if DOWNLOAD_PRIMARY and not has_auth_configuration():
		raise ValueError(
			"Informe EARTHDATA_TOKEN, EARTHDATA_USERNAME/EARTHDATA_PASSWORD, um credentials.txt "
			"com essas chaves, ou configure .netrc para habilitar download."
		)


def has_auth_configuration() -> bool:
	if EARTHDATA_TOKEN or (EARTHDATA_USERNAME and EARTHDATA_PASSWORD):
		return True
	if os.getenv("EARTHDATA_TOKEN"):
		return True
	if os.getenv("EARTHDATA_USERNAME") and os.getenv("EARTHDATA_PASSWORD"):
		return True
	if Path.home().joinpath("_netrc").exists() or Path.home().joinpath(".netrc").exists():
		return True
	if CREDENTIALS_FILE.exists():
		creds = load_credentials(CREDENTIALS_FILE)
		return bool(
			creds.get("EARTHDATA_TOKEN")
			or (creds.get("EARTHDATA_USERNAME") and creds.get("EARTHDATA_PASSWORD"))
		)
	return False


def normalize_text(raw_value: str) -> str:
	value = raw_value.strip().replace("\ufeff", "")
	if (value.startswith('"') and value.endswith('"')) or (
		value.startswith("'") and value.endswith("'")
	):
		value = value[1:-1].strip()
	return value


def load_credentials(file_path: Path) -> dict[str, str]:
	creds: dict[str, str] = {}
	if not file_path.exists():
		return creds

	with file_path.open("r", encoding="utf-8") as file_handle:
		for line in file_handle:
			line = line.strip()
			if not line or line.startswith("#") or "=" not in line:
				continue
			key, value = line.split("=", 1)
			creds[key.strip()] = normalize_text(value)

	return creds


def build_session() -> Session:
	session = requests.Session()
	creds = load_credentials(CREDENTIALS_FILE)
	token = normalize_text(
		EARTHDATA_TOKEN
		or os.getenv("EARTHDATA_TOKEN", "")
		or creds.get("EARTHDATA_TOKEN", "")
	)
	username = normalize_text(
		EARTHDATA_USERNAME
		or os.getenv("EARTHDATA_USERNAME", "")
		or creds.get("EARTHDATA_USERNAME", "")
	)
	password = normalize_text(
		EARTHDATA_PASSWORD
		or os.getenv("EARTHDATA_PASSWORD", "")
		or creds.get("EARTHDATA_PASSWORD", "")
	)

	if token:
		session.headers.update({"Authorization": f"Bearer {token}"})
	elif username and password:
		session.auth = (username, password)

	return session


def sanitize_name(value: str) -> str:
	cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
	return cleaned.strip("._") or "unnamed"


def normalize_dataset(dataset: str) -> str:
	return dataset.strip().upper()


def format_orbit_value(value: int | str | None) -> str | None:
	if value in (None, ""):
		return None
	return str(value)


def read_search_areas(geojson_path: str | None) -> list[dict[str, str | int | None]]:
	if not geojson_path:
		return [{"query_name": "orbit_only", "feature_index": None, "wkt": None}]

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
	areas: list[dict[str, str | int | None]] = []

	for idx, row in gdf.iterrows():
		geom = row.geometry
		parts = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
		for part_index, part in enumerate(parts, start=1):
			if part.geom_type != "Polygon":
				continue
			areas.append(
				{
					"query_name": sanitize_name(f"feature_{idx}_part_{part_index}"),
					"feature_index": idx,
					"wkt": part.wkt,
				}
			)

	if not areas:
		raise ValueError("Nenhum Polygon ou MultiPolygon foi encontrado no GeoJSON.")

	return areas


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


def record_intersects_validation_aoi(record: dict[str, Any], validation_geometry) -> bool:
	if validation_geometry is None:
		return True
	footprint_wkt = record.get("stringFootprint") or record.get("wkt")
	if not isinstance(footprint_wkt, str) or not footprint_wkt.strip():
		return False
	geometry = shapely_wkt.loads(footprint_wkt)
	if geometry.is_empty:
		return False
	return bool(geometry.intersects(validation_geometry))


def build_search_params(
	dataset: str,
	processing_level: str | None,
	area_wkt: str | None,
) -> dict[str, str | int]:
	params: dict[str, str | int] = {
		"dataset": normalize_dataset(dataset),
		"output": "json",
	}
	if processing_level:
		params["processingLevel"] = processing_level
	if DATE_START:
		params["start"] = DATE_START
	if DATE_END:
		params["end"] = DATE_END
	if area_wkt:
		params["intersectsWith"] = area_wkt
	if RELATIVE_ORBIT is not None:
		params["relativeOrbit"] = RELATIVE_ORBIT
	if ABSOLUTE_ORBIT is not None:
		params["absoluteOrbit"] = ABSOLUTE_ORBIT
	if FRAME is not None:
		params["frame"] = FRAME
	if ASF_FRAME is not None:
		params["asfframe"] = ASF_FRAME
	if FLIGHT_DIRECTION:
		params["flightDirection"] = FLIGHT_DIRECTION
	if MAX_RESULTS is not None:
		params["maxResults"] = MAX_RESULTS
	return params


def extract_records(payload: Any) -> list[dict[str, Any]]:
	if isinstance(payload, list):
		if payload and isinstance(payload[0], list):
			return [item for group in payload if isinstance(group, list) for item in group if isinstance(item, dict)]
		return [item for item in payload if isinstance(item, dict)]
	raise ValueError("Resposta inesperada da Search API do ASF.")


def search_records(
	dataset: str,
	processing_level: str | None,
	area_wkt: str | None,
) -> list[dict[str, Any]]:
	params = build_search_params(dataset=dataset, processing_level=processing_level, area_wkt=area_wkt)
	response = requests.get(SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
	response.raise_for_status()
	return extract_records(response.json())


def choose_asset_urls(dataset: str, record: dict[str, Any]) -> list[dict[str, str]]:
	urls: list[dict[str, str]] = []
	primary_url = record.get("downloadUrl")
	file_name = record.get("fileName") or record.get("granuleName") or record.get("sceneId")
	if DOWNLOAD_PRIMARY and isinstance(primary_url, str) and primary_url.strip():
		urls.append(
			{
				"asset_key": "primary",
				"url": primary_url,
				"filename": file_name or primary_url.rstrip("/").rsplit("/", 1)[-1],
			}
		)

	if DOWNLOAD_NISAR_KML and normalize_dataset(dataset) == "NISAR":
		nisar = record.get("nisar")
		if isinstance(nisar, dict):
			for extra_url in nisar.get("additionalUrls", []):
				if isinstance(extra_url, str) and extra_url.lower().endswith(".kml"):
					urls.append(
						{
							"asset_key": "kml",
							"url": extra_url,
							"filename": extra_url.rstrip("/").rsplit("/", 1)[-1],
						}
					)
					break

	return urls


def format_size_mb(size_bytes: int) -> str:
	return f"{size_bytes / (1024 * 1024):.1f} MB"


def download_file(session: Session, url: str, destination: Path) -> str:
	destination.parent.mkdir(parents=True, exist_ok=True)
	if destination.exists() and SKIP_EXISTING:
		return "skipped"

	with session.get(url, stream=True, timeout=REQUEST_TIMEOUT) as response:
		response.raise_for_status()
		total = int(response.headers.get("content-length", 0))
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


def write_manifest(output_dir: Path, rows: Iterable[dict[str, Any]]) -> Path:
	manifest_path = output_dir / "download_manifest.csv"
	rows = list(rows)
	fieldnames = [
		"query_name",
		"feature_index",
		"dataset",
		"platform",
		"product_id",
		"file_name",
		"asset_key",
		"download_url",
		"processing_level",
		"flight_direction",
		"relative_orbit",
		"absolute_orbit",
		"frame_number",
		"start_time",
		"stop_time",
		"size_mb",
		"download_path",
		"status",
	]
	with manifest_path.open("w", newline="", encoding="utf-8") as csvfile:
		writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)
	return manifest_path


def main() -> None:
	validate_configuration()
	output_dir = Path(OUTPUT_DIR).resolve()
	output_dir.mkdir(parents=True, exist_ok=True)
	areas = read_search_areas(SEARCH_GEOJSON_PATH)
	validation_geometry = read_validation_geometry(VALIDATION_GEOJSON_PATH)
	session = build_session()
	datasets = [normalize_dataset(dataset) for dataset in DATASETS]

	manifest_rows: list[dict[str, Any]] = []
	seen_assets: set[tuple[str, str]] = set()
	total_records_found = 0

	for dataset in datasets:
		processing_level = PROCESSING_LEVEL_BY_DATASET.get(dataset)
		for area in areas:
			query_name = str(area["query_name"])
			feature_index = area["feature_index"]
			print(f"Consultando area '{query_name}' no dataset {dataset}...")
			records = search_records(
				dataset=dataset,
				processing_level=processing_level,
				area_wkt=area["wkt"] if isinstance(area["wkt"], str) else None,
			)
			total_records_found += len(records)
			print(f"  {len(records)} produto(s) encontrado(s).")

			for record in records:
				if not record_intersects_validation_aoi(record, validation_geometry):
					continue
				product_id = str(
					record.get("product_file_id")
					or record.get("sceneId")
					or record.get("granuleName")
				)
				for asset in choose_asset_urls(dataset=dataset, record=record):
					dedupe_key = (product_id, asset["asset_key"])
					dataset_dir = output_dir / sanitize_name(dataset)
					destination = dataset_dir / sanitize_name(asset["filename"])

					if dedupe_key in seen_assets:
						status = "already_listed"
					else:
						status = download_file(session=session, url=asset["url"], destination=destination)
						seen_assets.add(dedupe_key)

					manifest_rows.append(
						{
							"query_name": query_name,
							"feature_index": feature_index,
							"dataset": dataset,
							"platform": record.get("platform", ""),
							"product_id": product_id,
							"file_name": asset["filename"],
							"asset_key": asset["asset_key"],
							"download_url": asset["url"],
							"processing_level": record.get("processingLevel", ""),
							"flight_direction": record.get("flightDirection", ""),
							"relative_orbit": format_orbit_value(record.get("relativeOrbit")),
							"absolute_orbit": format_orbit_value(record.get("absoluteOrbit")),
							"frame_number": format_orbit_value(record.get("frameNumber")),
							"start_time": record.get("startTime", ""),
							"stop_time": record.get("stopTime", ""),
							"size_mb": record.get("sizeMB", ""),
							"download_path": str(destination),
							"status": status,
						}
					)

	manifest_path = write_manifest(output_dir=output_dir, rows=manifest_rows)
	summary = {
		"datasets": datasets,
		"processing_level_by_dataset": PROCESSING_LEVEL_BY_DATASET,
			"search_geojson_path": SEARCH_GEOJSON_PATH,
			"validation_geojson_path": VALIDATION_GEOJSON_PATH,
		"date_start": DATE_START,
		"date_end": DATE_END,
		"relative_orbit": RELATIVE_ORBIT,
		"absolute_orbit": ABSOLUTE_ORBIT,
		"frame": FRAME,
		"asf_frame": ASF_FRAME,
		"flight_direction": FLIGHT_DIRECTION,
		"output_dir": str(output_dir),
		"total_queries": len(areas),
		"total_records_found": total_records_found,
		"total_manifest_rows": len(manifest_rows),
		"manifest": str(manifest_path),
	}
	print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
	main()