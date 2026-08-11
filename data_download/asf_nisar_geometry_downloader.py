"""Busca geometrias de cenas NISAR na Search API do ASF.

Mantem um fluxo robusto de busca (AOI + janelas temporais + retries),
mas exporta geometrias por cena unica, sem mescla por orbita.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import requests
from shapely.geometry import box
from shapely.geometry.polygon import orient
from shapely.validation import make_valid
from shapely.wkt import loads as load_wkt_text

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "datasets" / "asf_scenes_nisar"
SEARCH_URL = "https://api.daac.asf.alaska.edu/services/search/param"
REQUEST_TIMEOUT = 120
DEFAULT_START = "2025-11-01T00:00:00Z"
DEFAULT_END = "2026-07-31T23:59:59Z"
DEFAULT_PROCESSING_LEVEL = "GSLC"

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
PLATFORM_FILTERS: list[str] | None = None
RELATIVE_ORBIT_FILTERS: list[int] | None = None
FLIGHT_DIRECTION = None  # "ASCENDING" ou "DESCENDING"
MAX_RESULTS_PER_QUERY = None
AOI_SIMPLIFY_TOLERANCE = 0.0004
MAX_WKT_CHARS = 6000
MAX_SPLIT_DEPTH = 6
SEARCH_MAX_RETRIES = 6
SEARCH_RETRY_BACKOFF_SECONDS = 3
MAX_TIME_SPLIT_DEPTH_ON_504 = 6


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


def validate_configuration() -> None:
    if not OUTPUT_DIR:
        raise ValueError("Defina OUTPUT_DIR com o diretorio de saida.")
    if not PROCESSING_LEVEL:
        raise ValueError("Defina PROCESSING_LEVEL para a busca NISAR.")


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
    return gdf.geometry.union_all()


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


def iter_search_tasks(areas: list[SearchArea]) -> Iterable[SearchTask]:
    platforms = PLATFORM_FILTERS if PLATFORM_FILTERS else [None]
    relative_orbits = RELATIVE_ORBIT_FILTERS if RELATIVE_ORBIT_FILTERS else [None]
    for area in areas:
        for start, end in build_time_windows():
            for platform in platforms:
                for relative_orbit in relative_orbits:
                    yield SearchTask(
                        query_name=area.query_name,
                        feature_index=area.feature_index,
                        wkt=area.wkt,
                        start=start,
                        end=end,
                        platform=platform,
                        relative_orbit=relative_orbit,
                    )


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        if payload and isinstance(payload[0], list):
            return [item for group in payload if isinstance(group, list) for item in group if isinstance(item, dict)]
        return [item for item in payload if isinstance(item, dict)]
    raise ValueError("Resposta inesperada da Search API do ASF.")


def fetch_with_retries(params: dict[str, Any]) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(1, SEARCH_MAX_RETRIES + 1):
        try:
            response = requests.get(SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return extract_records(response.json())
        except requests.exceptions.RequestException as error:
            last_error = error
            status_code = None
            if isinstance(error, requests.exceptions.HTTPError) and error.response is not None:
                status_code = error.response.status_code

            retryable_http = status_code in {429, 500, 502, 503, 504}
            retryable_network = isinstance(error, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))
            if not (retryable_http or retryable_network) or attempt == SEARCH_MAX_RETRIES:
                break

            wait_seconds = SEARCH_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
            print(
                f"  tentativa {attempt}/{SEARCH_MAX_RETRIES} falhou (status={status_code or 'n/a'}). "
                f"nova tentativa em {wait_seconds}s...",
                flush=True,
            )
            time.sleep(wait_seconds)

    if last_error is None:
        raise RuntimeError("Falha desconhecida na busca do ASF.")
    raise last_error


def split_task_time_window(task: SearchTask) -> tuple[SearchTask, SearchTask] | None:
    start_dt = parse_iso_datetime(task.start)
    end_dt = parse_iso_datetime(task.end)
    if end_dt <= start_dt:
        return None
    seconds = int((end_dt - start_dt).total_seconds())
    if seconds <= 1:
        return None

    middle_dt = start_dt + (end_dt - start_dt) / 2
    left = SearchTask(
        query_name=task.query_name,
        feature_index=task.feature_index,
        wkt=task.wkt,
        start=format_iso_datetime(start_dt),
        end=format_iso_datetime(middle_dt),
        platform=task.platform,
        relative_orbit=task.relative_orbit,
    )
    right = SearchTask(
        query_name=task.query_name,
        feature_index=task.feature_index,
        wkt=task.wkt,
        start=format_iso_datetime(middle_dt),
        end=format_iso_datetime(end_dt),
        platform=task.platform,
        relative_orbit=task.relative_orbit,
    )
    return left, right


def search_products(task: SearchTask, split_depth: int = 0) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "dataset": "NISAR",
        "processingLevel": PROCESSING_LEVEL,
        "start": task.start,
        "end": task.end,
        "output": "json",
    }
    if task.wkt:
        params["intersectsWith"] = task.wkt
    if task.platform:
        params["platform"] = task.platform
    if task.relative_orbit is not None:
        params["relativeOrbit"] = task.relative_orbit
    if FLIGHT_DIRECTION:
        params["flightDirection"] = FLIGHT_DIRECTION
    if MAX_RESULTS_PER_QUERY is not None:
        params["maxResults"] = MAX_RESULTS_PER_QUERY

    try:
        return fetch_with_retries(params)
    except requests.exceptions.HTTPError as error:
        status_code = error.response.status_code if error.response is not None else None
        if status_code == 504 and split_depth < MAX_TIME_SPLIT_DEPTH_ON_504:
            split = split_task_time_window(task)
            if split is None:
                raise
            print(f"[NISAR] 504 em {task.start} -> {task.end}; dividindo janela temporal.", flush=True)
            left_task, right_task = split
            return search_products(left_task, split_depth + 1) + search_products(right_task, split_depth + 1)
        raise


def collect_scene_geometries() -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    filter_geometry = read_filter_geometry(VALIDATION_GEOJSON_PATH) if VALIDATION_GEOJSON_PATH else None
    areas = read_search_areas(SEARCH_GEOJSON_PATH)

    scene_map: dict[str, dict[str, Any]] = {}
    total_raw_records = 0
    failed_tasks = 0

    for task_index, task in enumerate(iter_search_tasks(areas), start=1):
        try:
            results = search_products(task)
        except Exception as error:
            failed_tasks += 1
            print(
                f"[NISAR] tarefa {task_index} falhou em {task.start} -> {task.end}, "
                f"platform={task.platform}, relativeOrbit={task.relative_orbit}: {error}",
                flush=True,
            )
            continue

        if not results:
            continue

        total_raw_records += len(results)
        print(
            f"[NISAR] tarefa {task_index}: {len(results)} cena(s) em {task.start} -> {task.end}, "
            f"platform={task.platform}, relativeOrbit={task.relative_orbit}",
            flush=True,
        )

        for feature in results:
            properties = dict(feature)
            try:
                geometry_wkt_text = properties.get("stringFootprint")
                if not geometry_wkt_text:
                    continue
                geometry = make_valid(load_wkt_text(geometry_wkt_text))
                if geometry.is_empty:
                    continue
                if task.platform and properties.get("platform") != task.platform:
                    continue
                if filter_geometry is not None and not geometry.intersects(filter_geometry):
                    continue

                product_id = str(
                    properties.get("product_file_id")
                    or properties.get("sceneId")
                    or properties.get("granuleName")
                    or properties.get("fileID")
                    or ""
                )
                if not product_id or product_id in scene_map:
                    continue

                scene_map[product_id] = {
                    "query_name": task.query_name,
                    "feature_index": task.feature_index,
                    "dataset": "NISAR",
                    "platform": properties.get("platform"),
                    "product_id": product_id,
                    "processing_level": properties.get("processingLevel"),
                    "flight_direction": properties.get("flightDirection"),
                    "relative_orbit": properties.get("relativeOrbit"),
                    "absolute_orbit": properties.get("absoluteOrbit") or properties.get("orbit"),
                    "frame_number": properties.get("frameNumber"),
                    "start_time": properties.get("startTime"),
                    "stop_time": properties.get("stopTime"),
                    "scene_name": properties.get("sceneName") or properties.get("granuleName") or properties.get("fileName"),
                    "geometry": geometry,
                }
            except Exception as error:
                scene_name = properties.get("sceneName") or properties.get("granuleName") or properties.get("fileName") or "unknown_scene"
                print(f"[NISAR] cena ignorada {scene_name}: {error}", flush=True)
                continue

    rows = list(scene_map.values())
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    if not gdf.empty:
        gdf = gdf.sort_values(["start_time", "platform", "relative_orbit"], na_position="last").reset_index(drop=True)

    stats = {
        "total_queries": len(areas),
        "total_raw_records": total_raw_records,
        "total_unique_scenes": len(rows),
        "failed_tasks": failed_tasks,
    }
    return gdf, stats


def export_outputs(gdf: gpd.GeoDataFrame) -> tuple[Path, Path]:
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = output_dir / "nisar_scene_geometries.geojson"
    csv_path = output_dir / "nisar_scene_geometries.csv"

    if gdf.empty:
        geojson_path.write_text(
            json.dumps({"type": "FeatureCollection", "features": []}, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        csv_path.write_text("", encoding="utf-8")
        return geojson_path, csv_path

    gdf.to_file(geojson_path, driver="GeoJSON")
    gdf.drop(columns="geometry").to_csv(csv_path, index=False)
    return geojson_path, csv_path


def main() -> None:
    validate_configuration()
    gdf, stats = collect_scene_geometries()
    geojson_path, csv_path = export_outputs(gdf)

    summary = {
        "dataset": "NISAR",
        "processing_level": PROCESSING_LEVEL,
        "search_geojson_path": SEARCH_GEOJSON_PATH,
        "validation_geojson_path": VALIDATION_GEOJSON_PATH,
        "date_start": DATE_START or DEFAULT_START,
        "date_end": DATE_END or DEFAULT_END,
        "window_months": WINDOW_MONTHS,
        "platform_filters": PLATFORM_FILTERS,
        "relative_orbit_filters": RELATIVE_ORBIT_FILTERS,
        "flight_direction": FLIGHT_DIRECTION,
        "output_dir": str(Path(OUTPUT_DIR).resolve()),
        "scene_geometries_geojson": str(geojson_path),
        "scene_geometries_csv": str(csv_path),
        **stats,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
