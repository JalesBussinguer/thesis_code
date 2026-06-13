"""Download de imagens NISAR diretamente do portal ASF Vertex.

Edite os parametros na secao CONFIGURACAO e execute o arquivo diretamente.

O fluxo usa a biblioteca asf_search para localizar cenas NISAR por janela
 temporal, AOI, nivel de processamento, orbita e direcao de voo.
Os downloads sao autenticados com credenciais Earthdata Login.
"""

from __future__ import annotations

import csv
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asf_search as asf
import geopandas as gpd
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.validation import make_valid

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "downloads" / "nisar_asf"


# =========================
# CONFIGURACAO DO USUARIO
# =========================
OUTPUT_DIR = Path("H:/nisar_data")
SEARCH_GEOJSON_PATH = "datasets/cerrado_bbox.geojson"
VALIDATION_GEOJSON_PATH = "datasets/cerrado_border.geojson"
DATE_START = "2025-11-01T00:00:00Z"  # inicio da busca (formato ISO 8601 UTC)
DATE_END = "2026-06-12T23:59:59Z"    # fim da busca  (None = agora)
PROCESSING_LEVEL = "RSLC"            # RSLC e o nivel mais comum para NISAR
WINDOW_MONTHS = 1
RELATIVE_ORBIT_FILTERS: list[int] | None = None
PATH_NUMBER_FILTERS: list[int] | None = None
FLIGHT_DIRECTION: str | None = None   # asf.FLIGHT_DIRECTION.ASCENDING ou DESCENDING
MAX_RESULTS_PER_QUERY: int | None = None
SKIP_EXISTING = True
CMR_TIMEOUT_SECONDS = 120  # Aumentado de 30s para evitar timeouts em buscas grandes

# Credenciais Earthdata Login
# Metodo recomendado: configure ~/.netrc com:
#   machine urs.earthdata.nasa.gov login <usuario> password <senha>
# Ou adicione ao asf_credentials.txt (na raiz do projeto):
#   EARTHDATA_USERNAME=seu_usuario
#   EARTHDATA_PASSWORD=sua_senha
# Ou defina um token pessoal Earthdata:
#   EARTHDATA_TOKEN=seu_token
EARTHDATA_TOKEN: str | None = None
EARTHDATA_USERNAME: str | None = None
EARTHDATA_PASSWORD: str | None = None
CREDENTIALS_FILE = ROOT_DIR / "asf_credentials.txt"


def _resolve_path(path: str | Path | None) -> Path | None:
	if path is None:
		return None
	p = Path(path)
	return p if p.is_absolute() else ROOT_DIR / p


def load_credentials(file_path: Path) -> dict[str, str]:
	creds: dict[str, str] = {}
	if not file_path.exists():
		return creds
	with file_path.open("r", encoding="utf-8") as fh:
		for line in fh:
			line = line.strip()
			if not line or line.startswith("#") or "=" not in line:
				continue
			key, value = line.split("=", 1)
			value = value.strip().strip("\"'")
			creds[key.strip()] = value
	return creds


def _normalize_credential(value: str | None) -> str | None:
	if not value:
		return None
	stripped = value.strip().strip("\"'")
	return stripped or None


def has_auth_configuration() -> bool:
	creds = load_credentials(CREDENTIALS_FILE)
	has_token = bool(
		EARTHDATA_TOKEN
		or os.getenv("EARTHDATA_TOKEN")
		or creds.get("EARTHDATA_TOKEN")
	)
	has_creds = bool(
		(EARTHDATA_USERNAME or os.getenv("EARTHDATA_USERNAME") or creds.get("EARTHDATA_USERNAME"))
		and (EARTHDATA_PASSWORD or os.getenv("EARTHDATA_PASSWORD") or creds.get("EARTHDATA_PASSWORD"))
	)
	has_netrc = (
		Path.home().joinpath(".netrc").exists()
		or Path.home().joinpath("_netrc").exists()
	)
	return has_token or has_creds or has_netrc


def has_esa_maap_credentials_only() -> bool:
	if not CREDENTIALS_FILE.exists():
		return False
	creds = load_credentials(CREDENTIALS_FILE)
	has_earthdata = bool(
		creds.get("EARTHDATA_TOKEN")
		or (creds.get("EARTHDATA_USERNAME") and creds.get("EARTHDATA_PASSWORD"))
	)
	has_esa_maap = bool(
		creds.get("CLIENT_ID") or creds.get("CLIENT_SECRET") or creds.get("OFFLINE_TOKEN")
	)
	return has_esa_maap and not has_earthdata


def build_asf_session() -> asf.ASFSession:
	"""Return an ASFSession authenticated with the best available credential."""
	creds = load_credentials(CREDENTIALS_FILE)
	token = _normalize_credential(
		EARTHDATA_TOKEN or os.getenv("EARTHDATA_TOKEN") or creds.get("EARTHDATA_TOKEN")
	)
	username = _normalize_credential(
		EARTHDATA_USERNAME or os.getenv("EARTHDATA_USERNAME") or creds.get("EARTHDATA_USERNAME")
	)
	password = _normalize_credential(
		EARTHDATA_PASSWORD or os.getenv("EARTHDATA_PASSWORD") or creds.get("EARTHDATA_PASSWORD")
	)
	session = asf.ASFSession()
	if token:
		session.auth_with_token(token)
	elif username and password:
		session.auth_with_creds(username, password)
	# else: relies on .netrc configured for urs.earthdata.nasa.gov
	return session


def read_search_aoi_wkt(geojson_path: str | None) -> str | None:
	"""Read a GeoJSON file and return a single unified WKT string for ASF search."""
	resolved = _resolve_path(geojson_path)
	if resolved is None:
		return None
	gdf = gpd.read_file(resolved)
	if gdf.empty:
		raise ValueError(f"GeoJSON vazio: {resolved}")
	if gdf.crs is not None:
		gdf = gdf.to_crs("EPSG:4326")
	gdf = gdf.loc[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()
	if gdf.empty:
		raise ValueError(f"Nenhuma geometria valida em: {resolved}")
	gdf["geometry"] = gdf.geometry.apply(make_valid)
	return unary_union(list(gdf.geometry)).wkt


def read_validation_geometry(geojson_path: str | None) -> Any:
	"""Load validation geometry for footprint intersection checking."""
	resolved = _resolve_path(geojson_path)
	if resolved is None:
		return None
	gdf = gpd.read_file(resolved)
	if gdf.empty:
		raise ValueError(f"GeoJSON vazio: {resolved}")
	if gdf.crs is not None:
		gdf = gdf.to_crs("EPSG:4326")
	gdf = gdf.loc[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()
	if gdf.empty:
		raise ValueError(f"Nenhuma geometria valida em: {resolved}")
	gdf["geometry"] = gdf.geometry.apply(make_valid)
	return unary_union(list(gdf.geometry))


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
	if not DATE_START:
		raise ValueError("Defina DATE_START com a data de inicio da busca (ex: '2026-01-01T00:00:00Z').")
	start_dt = parse_iso_datetime(DATE_START)
	end_dt = parse_iso_datetime(DATE_END) if DATE_END else datetime.now(UTC)
	if start_dt >= end_dt:
		raise ValueError("DATE_START precisa ser anterior a DATE_END.")
	if WINDOW_MONTHS <= 0:
		return [(format_iso_datetime(start_dt), format_iso_datetime(end_dt))]
	windows: list[tuple[str, str]] = []
	current = start_dt
	while current < end_dt:
		nxt = add_months(current, WINDOW_MONTHS)
		current_end = min(nxt, end_dt)
		windows.append((format_iso_datetime(current), format_iso_datetime(current_end)))
		current = current_end
	return windows


def search_window(
	search_wkt: str | None,
	start: str,
	end: str,
	relative_orbit: int | None,
) -> asf.ASFSearchResults:
	"""Run a single asf_search.search() call and return ASFSearchResults."""
	kwargs_dataset: dict[str, Any] = {
		"dataset": "NISAR",
		"processingLevel": [PROCESSING_LEVEL],
		"start": start,
		"end": end,
	}
	if search_wkt:
		kwargs_dataset["intersectsWith"] = search_wkt
	if relative_orbit is not None:
		kwargs_dataset["relativeOrbit"] = relative_orbit
	if FLIGHT_DIRECTION:
		kwargs_dataset["flightDirection"] = FLIGHT_DIRECTION
	if MAX_RESULTS_PER_QUERY is not None:
		kwargs_dataset["maxResults"] = MAX_RESULTS_PER_QUERY

	try:
		return asf.search(**kwargs_dataset)
	except Exception:
		# Fallback para versoes de asf_search que exigem platform ao inves de dataset.
		kwargs_platform = dict(kwargs_dataset)
		kwargs_platform.pop("dataset", None)
		kwargs_platform["platform"] = ["NISAR"]
		return asf.search(**kwargs_platform)


def product_intersects_validation(
	product: asf.ASFProduct,
	validation_geometry: Any,
) -> bool:
	if validation_geometry is None:
		return True
	if not product.geometry:
		return False
	footprint = shape(product.geometry)
	return not footprint.is_empty and bool(footprint.intersects(validation_geometry))


def product_matches_path_filter(product: asf.ASFProduct) -> bool:
	if not PATH_NUMBER_FILTERS:
		return True
	props = product.properties
	path_number = props.get("pathNumber") or props.get("track")
	if path_number in (None, ""):
		return False
	try:
		path_int = int(path_number)
	except (TypeError, ValueError):
		return False
	return path_int in PATH_NUMBER_FILTERS


def write_manifest(output_dir: Path, rows: list[dict[str, Any]]) -> Path:
	manifest_path = output_dir / "download_manifest.csv"
	fieldnames = [
		"product_id",
		"scene_name",
		"granule_name",
		"platform",
		"processing_level",
		"flight_direction",
		"path_number",
		"relative_orbit",
		"absolute_orbit",
		"frame_number",
		"beam_mode",
		"start_time",
		"stop_time",
		"size_mb",
		"download_url",
		"filename",
		"download_path",
		"status",
	]
	with manifest_path.open("w", newline="", encoding="utf-8") as csvfile:
		writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction="ignore")
		writer.writeheader()
		writer.writerows(rows)
	return manifest_path


def main() -> None:
	# Configurar timeout aumentado para CMR para evitar timeouts em buscas grandes
	asf.constants.INTERNAL.CMR_TIMEOUT = CMR_TIMEOUT_SECONDS

	output_dir = Path(OUTPUT_DIR).resolve()
	output_dir.mkdir(parents=True, exist_ok=True)

	auth_available = has_auth_configuration()
	if not auth_available:
		if has_esa_maap_credentials_only():
			print(
				"[NISAR] asf_credentials.txt contem credenciais ESA MAAP "
				"(CLIENT_ID/CLIENT_SECRET/OFFLINE_TOKEN), nao Earthdata. "
				"Downloads serao pulados.",
				flush=True,
			)
		else:
			print(
				"[NISAR] Nenhuma credencial Earthdata encontrada. "
				"A busca sera executada, mas downloads novos serao marcados como missing_auth.",
				flush=True,
			)

	session = build_asf_session()
	search_wkt = read_search_aoi_wkt(SEARCH_GEOJSON_PATH)
	validation_geometry = read_validation_geometry(VALIDATION_GEOJSON_PATH)
	windows = build_time_windows()
	relative_orbits: list[int | None] = RELATIVE_ORBIT_FILTERS or [None]

	# Collect all matching products, deduplicated by fileID/granuleName.
	seen: dict[str, asf.ASFProduct] = {}
	total_found = 0

	for start, end in windows:
		for relative_orbit in relative_orbits:
			try:
				results = search_window(search_wkt, start, end, relative_orbit)
			except Exception as err:
				print(
					f"[NISAR] Busca falhou ({start} -> {end}, "
					f"relativeOrbit={relative_orbit}): {err}",
					flush=True,
				)
				continue

			if not results:
				continue

			total_found += len(results)
			print(
				f"[NISAR] {len(results)} cena(s) | {start} -> {end} | "
				f"relativeOrbit={relative_orbit}",
				flush=True,
			)

			for product in results:
				props = product.properties
				pid = (
					props.get("fileID")
					or props.get("granuleName")
					or props.get("sceneName")
					or ""
				)
				if not pid or pid in seen:
					continue
				if not product_intersects_validation(product, validation_geometry):
					continue
				if not product_matches_path_filter(product):
					continue
				seen[pid] = product

	print(f"[NISAR] Total bruto da busca: {total_found}", flush=True)
	print(f"[NISAR] Cenas unicas apos filtragem: {len(seen)}", flush=True)

	manifest_rows: list[dict[str, Any]] = []
	products_list = list(seen.values())
	total = len(products_list)
	downloaded_count = skipped_count = error_count = 0

	print(f"[NISAR] Iniciando downloads: {total} cena(s) em {output_dir}", flush=True)
	print(f"[NISAR] {'=' * 60}", flush=True)

	for idx, product in enumerate(products_list, start=1):
		props = product.properties
		url = props.get("url") or props.get("downloadUrl") or ""
		filename = (
			props.get("fileName")
			or props.get("granuleName")
			or props.get("sceneName")
			or "unknown"
		)
		destination = output_dir / filename
		size_mb = props.get("sizeMB", "")
		size_label = f" ({size_mb} MB)" if size_mb else ""
		prefix = f"[{idx:>{len(str(total))}}/{total}]"

		if not url:
			status = "no_url"
			print(f"[NISAR] {prefix} SEM URL   - {filename}", flush=True)
		elif destination.exists() and SKIP_EXISTING:
			status = "skipped"
			skipped_count += 1
			print(f"[NISAR] {prefix} PULADO    - {filename}{size_label}", flush=True)
		elif not auth_available:
			status = "missing_auth"
			print(f"[NISAR] {prefix} SEM AUTH  - {filename}{size_label}", flush=True)
		else:
			print(f"[NISAR] {prefix} Baixando  - {filename}{size_label} ...", flush=True)
			try:
				asf.download_url(url=url, path=str(output_dir), filename=filename, session=session)
				status = "downloaded"
				downloaded_count += 1
				print(f"[NISAR] {prefix} OK        - {filename}", flush=True)
			except Exception as err:
				status = "error"
				error_count += 1
				print(f"[NISAR] {prefix} ERRO      - {filename}: {err}", flush=True)

		manifest_rows.append({
			"product_id": props.get("fileID") or props.get("granuleName") or props.get("sceneName", ""),
			"scene_name": props.get("sceneName", ""),
			"granule_name": props.get("granuleName", ""),
			"platform": props.get("platform", ""),
			"processing_level": props.get("processingLevel", ""),
			"flight_direction": props.get("flightDirection", ""),
			"path_number": props.get("pathNumber") or props.get("track", ""),
			"relative_orbit": props.get("relativeOrbit", ""),
			"absolute_orbit": props.get("absoluteOrbit") or props.get("orbit", ""),
			"frame_number": props.get("frameNumber", ""),
			"beam_mode": props.get("beamModeType", ""),
			"start_time": props.get("startTime", ""),
			"stop_time": props.get("stopTime", ""),
			"size_mb": props.get("sizeMB", ""),
			"download_url": url,
			"filename": filename,
			"download_path": str(destination),
			"status": status,
		})

	manifest_path = write_manifest(output_dir=output_dir, rows=manifest_rows)
	print(f"[NISAR] {'=' * 60}", flush=True)
	print(
		f"[NISAR] Resumo: {downloaded_count} baixado(s) | {skipped_count} pulado(s) "
		f"| {error_count} erro(s) | {len(manifest_rows)} total",
		flush=True,
	)
	print(f"[NISAR] Manifesto salvo em: {manifest_path}", flush=True)


if __name__ == "__main__":
	main()
