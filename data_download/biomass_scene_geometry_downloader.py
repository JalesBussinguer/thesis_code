"""Consolida geometrias de cenas BIOMASS disponiveis no catalogo STAC do ESA MAAP.

O script realiza buscas por janelas temporais configuradas pelo usuario,
usando a bounding box do Cerrado como filtro espacial. Para cada cena
encontrada, a geometria e os metadados sao extraidos diretamente do item
STAC. A checagem com a geometria real do Cerrado e aplicada apenas no
passo final, imediatamente antes da escrita do arquivo de saida.

Uso padrao:
    python data_download/biomass_scene_geometry_downloader.py
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urljoin

import geopandas as gpd
import requests
from pystac import Item
from pystac_client import Client
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

# ==========================
# CONSTANTES DO CATALOGO
# ==========================
CATALOG_URL = "https://catalog.maap.eo.esa.int/catalogue/"
TOKEN_URL = "https://iam.maap.eo.esa.int/realms/esa-maap/protocol/openid-connect/token"
CLIENT_ID = "offline-token"
CLIENT_SECRET = "p1eL7uonXs6MDxtGbgKdPVRAmnGxHpVE"

DEFAULT_PRODUCT_TYPE_BY_COLLECTION = {
    "BiomassLevel1a": "S1_SCS__1S",
    "BiomassLevel1b": "S1_DGM__1S",
}

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "datasets" / "biomass_scenes"
DEFAULT_START = "2025-11-01T00:00:00Z"
DEFAULT_END = "2026-06-12T23:59:59Z"
DEFAULT_TIMEOUT = 120

# ==========================
# CONFIGURACAO DO USUARIO
# ==========================
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
SEARCH_GEOJSON_PATH = "datasets/cerrado_bbox.geojson"
VALIDATION_GEOJSON_PATH = "datasets/cerrado_border.geojson"
COLLECTION = "BiomassLevel1a"
PRODUCT_TYPE = DEFAULT_PRODUCT_TYPE_BY_COLLECTION.get(COLLECTION)
ADDITIONAL_FILTER: str | None = None
DATE_START: str | None = None
DATE_END: str | None = None
WINDOW_MONTHS = 1
MAX_ITEMS: int | None = None
REQUEST_TIMEOUT = DEFAULT_TIMEOUT

# Informe apenas um dos campos de autenticacao.
ACCESS_TOKEN: str | None = None
OFFLINE_TOKEN: str | None = None
CREDENTIALS_FILE = Path("credentials.txt")


# ==========================
# DATACLASSES
# ==========================
@dataclass(frozen=True)
class SearchPolygon:
    feature_index: int
    polygon_name: str
    bbox: list[float]
    geometry: dict[str, object]


# ==========================
# AUTENTICACAO
# ==========================
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

    with file_path.open("r", encoding="utf-8") as fh:
        for line in fh:
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
    return RuntimeError(
        "Falha ao gerar access token no ESA MAAP. "
        f"Status HTTP: {response.status_code}. Resposta: {body}\n\n"
        "Verifique se o offline token em credentials.txt e valido e nao expirou."
    )


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
            "Missing OFFLINE_TOKEN. Defina ACCESS_TOKEN, OFFLINE_TOKEN, "
            "ESA_MAAP_OFFLINE_TOKEN ou credentials.txt."
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


# ==========================
# JANELAS TEMPORAIS
# ==========================
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
        [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
         31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1],
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


# ==========================
# GEOMETRIAS
# ==========================
def read_search_polygons(geojson_path: str) -> list[SearchPolygon]:
    from shapely.geometry import mapping

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
    polygons: list[SearchPolygon] = []
    for idx, row in gdf.iterrows():
        geom = row.geometry
        parts = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
        for part_index, part in enumerate(parts, start=1):
            if part.geom_type != "Polygon":
                continue
            polygons.append(
                SearchPolygon(
                    feature_index=int(idx),
                    polygon_name=f"feature_{idx}_part_{part_index}",
                    bbox=[float(part.bounds[0]), float(part.bounds[1]), float(part.bounds[2]), float(part.bounds[3])],
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
    gdf["geometry"] = gdf.geometry.apply(make_valid)
    return unary_union(list(gdf.geometry))


# ==========================
# BUSCA STAC
# ==========================
def build_cql2_filter(product_type: str | None, extra_filter: str | None) -> str | None:
    clauses: list[str] = []
    if product_type:
        clauses.append(f"product:type='{product_type}'")
    if extra_filter:
        clauses.append(extra_filter)
    if not clauses:
        return None
    return " and ".join(f"({clause})" for clause in clauses)


def iter_stac_items(
    search_url: str,
    collection: str,
    bbox: list[float],
    datetime_range: str,
    cql2_filter: str | None,
    max_items: int | None,
) -> Iterator[Item]:
    payload: dict[str, Any] = {
        "collections": [collection],
        "bbox": bbox,
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
    request_body: dict[str, Any] | None = json.loads(json.dumps(payload))
    visited_pages: set[tuple[str, str, str]] = set()

    while request_url:
        body_key = json.dumps(request_body, sort_keys=True) if request_body else ""
        page_key = (request_method.upper(), request_url, body_key)
        if page_key in visited_pages:
            break
        visited_pages.add(page_key)

        request_kwargs: dict[str, Any] = {"timeout": REQUEST_TIMEOUT}
        if request_method.upper() == "POST":
            request_kwargs["json"] = request_body or {}
        response = requests.request(request_method.upper(), request_url, **request_kwargs)

        if response.status_code >= 400:
            body = response.text.strip()
            if len(body) > 1200:
                body = body[:1200] + "..."
            raise RuntimeError(
                f"Erro na consulta STAC. collection={collection}; "
                f"datetime={datetime_range}; status={response.status_code}; detalhes={body}"
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
        next_body = next_link.get("body")
        request_body = json.loads(json.dumps(next_body)) if isinstance(next_body, dict) else None


# ==========================
# METADADOS DA CENA
# ==========================
def normalize_value(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def parse_product_fields(product_name: str) -> dict[str, str | None]:
    """Extrai mode, track e frame do nome do produto BIOMASS."""
    parts = [p for p in product_name.split("_") if p]
    mode = parts[2] if len(parts) > 2 else None
    track = parts[10] if len(parts) > 10 else None
    frame = parts[11] if len(parts) > 11 else None
    return {"mode": mode, "track": track, "frame": frame}


def extract_scene_record(item: Item) -> dict[str, Any]:
    props = item.properties or {}
    product_name = normalize_value(props.get("title")) or item.id
    parsed = parse_product_fields(product_name)

    orbit_state = normalize_value(props.get("sat:orbit_state"))
    if orbit_state:
        orbit_state = orbit_state.upper()

    raw_props = {k: v for k, v in props.items() if k not in
                 ("title", "start_datetime", "end_datetime",
                  "eofeos:repeat_cycle_id", "sat:orbit_state")}

    return {
        "scene_id": item.id,
        "product_name": product_name,
        "collection": COLLECTION,
        "mode": parsed["mode"],
        "track": parsed["track"],
        "frame": parsed["frame"],
        "start_datetime": normalize_value(props.get("start_datetime")),
        "end_datetime": normalize_value(props.get("end_datetime")),
        "repeat_cycle": normalize_value(props.get("eofeos:repeat_cycle_id")),
        "orbit_state": orbit_state,
        "raw_properties": json.dumps(raw_props, ensure_ascii=False),
    }


# ==========================
# COLETA PRINCIPAL
# ==========================
def collect_scene_geometries() -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    polygons = read_search_polygons(SEARCH_GEOJSON_PATH)
    cql2_filter = build_cql2_filter(PRODUCT_TYPE, ADDITIONAL_FILTER)
    time_windows = build_time_windows()

    catalog = Client.open(CATALOG_URL)
    search_link = catalog.get_search_link()
    if search_link is None or not search_link.target:
        raise RuntimeError("O catalogo STAC nao exibe link de busca (/search).")
    search_url = str(search_link.target)

    scene_rows: list[dict[str, Any]] = []
    total_raw = 0
    failed_windows = 0

    for polygon in polygons:
        for window_start, window_end in time_windows:
            datetime_range = f"{window_start}/{window_end}"
            print(
                f"[BIOMASS] buscando {polygon.polygon_name} | {datetime_range} ...",
                flush=True,
            )
            try:
                items = iter_stac_items(
                    search_url=search_url,
                    collection=COLLECTION,
                    bbox=polygon.bbox,
                    datetime_range=datetime_range,
                    cql2_filter=cql2_filter,
                    max_items=MAX_ITEMS,
                )
                window_count = 0
                for item in items:
                    total_raw += 1
                    window_count += 1

                    if item.geometry is None:
                        continue

                    try:
                        item_geometry = make_valid(shape(item.geometry))
                    except Exception:
                        continue

                    if item_geometry.is_empty:
                        continue

                    record = extract_scene_record(item)
                    record["geometry"] = item_geometry
                    scene_rows.append(record)

                print(
                    f"[BIOMASS] {window_count} cena(s) brutas; "
                    f"{len(scene_rows)} aceitas no total ate agora.",
                    flush=True,
                )
            except Exception as error:
                failed_windows += 1
                print(
                    f"[BIOMASS] janela {datetime_range} falhou para "
                    f"{polygon.polygon_name}: {error}",
                    flush=True,
                )
                continue

    gdf = gpd.GeoDataFrame(scene_rows, geometry="geometry", crs="EPSG:4326")
    if not gdf.empty:
        gdf = gdf.sort_values(
            ["track", "frame", "start_datetime"],
            na_position="last",
        ).reset_index(drop=True)

    stats: dict[str, Any] = {
        "total_search_polygons": len(polygons),
        "total_time_windows": len(time_windows),
        "total_raw_items": total_raw,
        "total_unique_scenes": len(scene_rows),
        "failed_windows": failed_windows,
    }
    return gdf, stats


def apply_final_validation_filter(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    validation_geometry = read_validation_geometry(VALIDATION_GEOJSON_PATH)
    if validation_geometry is None or gdf.empty:
        return gdf

    mask = gdf.geometry.apply(lambda geometry: make_valid(geometry).intersects(validation_geometry))
    return gdf.loc[mask].reset_index(drop=True)


# ==========================
# EXPORTACAO
# ==========================
def export_outputs(gdf: gpd.GeoDataFrame) -> tuple[Path, Path]:
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = output_dir / "biomass_scene_geometries.geojson"
    csv_path = output_dir / "biomass_scene_geometries.csv"

    gdf = apply_final_validation_filter(gdf)

    if gdf.empty:
        geojson_path.write_text(
            json.dumps({"type": "FeatureCollection", "features": []},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        csv_path.write_text("", encoding="utf-8")
        return geojson_path, csv_path

    gdf.to_file(geojson_path, driver="GeoJSON")
    gdf.drop(columns="geometry").to_csv(csv_path, index=False)
    return geojson_path, csv_path


# ==========================
# PONTO DE ENTRADA
# ==========================
def validate_configuration() -> None:
    if not SEARCH_GEOJSON_PATH:
        raise ValueError("Defina SEARCH_GEOJSON_PATH com o caminho do GeoJSON de busca.")
    if not COLLECTION:
        raise ValueError("Defina COLLECTION com a colecao STAC do BIOMASS.")
    if not OUTPUT_DIR:
        raise ValueError("Defina OUTPUT_DIR com o diretorio de saida.")
    if (
        not ACCESS_TOKEN
        and not OFFLINE_TOKEN
        and not os.getenv("ESA_MAAP_OFFLINE_TOKEN")
        and not CREDENTIALS_FILE.exists()
    ):
        raise ValueError(
            "Informe ACCESS_TOKEN, OFFLINE_TOKEN, ESA_MAAP_OFFLINE_TOKEN "
            "ou um credentials.txt valido."
        )


def main() -> None:
    validate_configuration()

    print(f"[BIOMASS] colecao: {COLLECTION}", flush=True)
    print(f"[BIOMASS] periodo: {DATE_START or DEFAULT_START} -> {DATE_END or DEFAULT_END}", flush=True)
    print(f"[BIOMASS] janela mensal: {WINDOW_MONTHS} mes(es)", flush=True)

    gdf, stats = collect_scene_geometries()
    geojson_path, csv_path = export_outputs(gdf)

    summary = {
        "collection": COLLECTION,
        "product_type": PRODUCT_TYPE,
        "search_geojson_path": SEARCH_GEOJSON_PATH,
        "validation_geojson_path": VALIDATION_GEOJSON_PATH,
        "date_start": DATE_START or DEFAULT_START,
        "date_end": DATE_END or DEFAULT_END,
        "window_months": WINDOW_MONTHS,
        "output_geojson": str(geojson_path),
        "output_csv": str(csv_path),
        **stats,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
