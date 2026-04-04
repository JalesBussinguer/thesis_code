from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

import requests

from ..models import AssetRecord, ProductRecord, ProviderDiscoveryContext, SearchArea
from ..orbit_keys import build_orbit_scope_key


SEARCH_URL = "https://api.daac.asf.alaska.edu/services/search/param"
REQUEST_TIMEOUT = 120
DEFAULT_PROCESSING_LEVEL_BY_DATASET = {
	"SENTINEL-1": "SLC",
	"NISAR": "RSLC",
}


def build_search_params(
	context: ProviderDiscoveryContext,
	orbit_row: Any,
	search_area: SearchArea,
) -> dict[str, Any]:
	params: dict[str, Any] = {
		"dataset": context.dataset.upper(),
		"output": "json",
		"start": context.window_start_utc,
		"end": context.window_end_utc,
		"intersectsWith": search_area.geometry_wkt,
		"processingLevel": orbit_row["processing_level"] or DEFAULT_PROCESSING_LEVEL_BY_DATASET[context.dataset.upper()],
	}
	if orbit_row["platform"]:
		params["platform"] = orbit_row["platform"]
	if orbit_row["path_number"] is not None:
		params["relativeOrbit"] = orbit_row["path_number"]
	if orbit_row["frame_number"] is not None:
		params["frame"] = orbit_row["frame_number"]
	if orbit_row["flight_direction"]:
		params["flightDirection"] = orbit_row["flight_direction"]
	if orbit_row["beam_mode"]:
		params["beamMode"] = orbit_row["beam_mode"]
	return params


def discover_records(
	context: ProviderDiscoveryContext,
	orbit_row: Any,
	request_get: Callable[..., Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
	request_get = request_get or requests.get
	seen_ids: set[str] = set()
	results: list[dict[str, Any]] = []
	fingerprints: list[str] = []
	for search_area in context.search_areas:
		params = build_search_params(context, orbit_row, search_area)
		fingerprints.append(json.dumps(params, sort_keys=True, ensure_ascii=True))
		response = request_get(SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
		response.raise_for_status()
		payload = response.json()
		for record in extract_records(payload):
			record_id = str(record.get("product_file_id") or record.get("sceneId") or record.get("granuleName") or record.get("fileName") or "")
			if not record_id or record_id in seen_ids:
				continue
			seen_ids.add(record_id)
			results.append(record)
	return results, _fingerprint_payload(fingerprints)


def extract_records(payload: Any) -> list[dict[str, Any]]:
	if isinstance(payload, list):
		if payload and isinstance(payload[0], list):
			return [item for group in payload if isinstance(group, list) for item in group if isinstance(item, dict)]
		return [item for item in payload if isinstance(item, dict)]
	raise ValueError("Unexpected ASF Search API response payload.")


def record_to_product(dataset: str, record: dict[str, Any]) -> ProductRecord:
	mapped = {
		"platform": record.get("platform"),
		"flight_direction": record.get("flightDirection"),
		"path_number": record.get("pathNumber") or record.get("track") or record.get("relativeOrbit"),
		"frame_number": record.get("frameNumber"),
		"beam_mode": record.get("beamModeType"),
	}
	orbit_scope_key = build_orbit_scope_key(dataset, mapped)
	return ProductRecord(
		dataset=dataset.upper(),
		provider_product_id=str(record.get("product_file_id") or record.get("sceneId") or record.get("granuleName") or record.get("fileName")),
		scene_name=record.get("sceneName") or record.get("granuleName") or record.get("fileName"),
		platform=record.get("platform"),
		processing_level=record.get("processingLevel"),
		orbit_scope_key=orbit_scope_key,
		relative_orbit=record.get("relativeOrbit"),
		absolute_orbit=record.get("absoluteOrbit"),
		path_number=record.get("pathNumber") or record.get("track") or record.get("relativeOrbit"),
		frame_number=record.get("frameNumber"),
		beam_mode=record.get("beamModeType"),
		flight_direction=record.get("flightDirection"),
		acquisition_start_utc=record.get("startTime"),
		acquisition_stop_utc=record.get("stopTime"),
		footprint_wkt=record.get("stringFootprint") or record.get("wkt"),
		metadata_json={"raw_record": record},
		assets=tuple(_choose_asset_urls(dataset.upper(), record)),
	)


def _choose_asset_urls(dataset: str, record: dict[str, Any]) -> list[AssetRecord]:
	assets: list[AssetRecord] = []
	primary_url = record.get("downloadUrl")
	filename = record.get("fileName") or record.get("granuleName") or record.get("sceneId")
	if isinstance(primary_url, str) and primary_url.strip():
		assets.append(AssetRecord(asset_key="primary", source_url=primary_url, filename=filename or primary_url.rstrip("/").rsplit("/", 1)[-1]))
	if dataset == "NISAR":
		nisar = record.get("nisar")
		if isinstance(nisar, dict):
			for extra_url in nisar.get("additionalUrls", []):
				if isinstance(extra_url, str) and extra_url.lower().endswith(".kml"):
					assets.append(AssetRecord(asset_key="kml", source_url=extra_url, filename=extra_url.rstrip("/").rsplit("/", 1)[-1], is_required=False, asset_type="kml"))
					break
	return assets


def _fingerprint_payload(parts: list[str]) -> str:
	joined = "||".join(parts)
	return hashlib.sha256(joined.encode("utf-8")).hexdigest()