from __future__ import annotations

import hashlib
import re
from typing import Any


def sanitize_component(value: Any, fallback: str = "UNKNOWN") -> str:
	text = str(value).strip() if value not in (None, "") else fallback
	cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
	return cleaned.strip("._") or fallback


def build_orbit_scope_key(dataset: str, record: dict[str, Any]) -> str:
	dataset_name = dataset.strip().upper()
	if dataset_name in {"SENTINEL-1", "NISAR"}:
		platform = sanitize_component(record.get("platform") or dataset_name)
		flight_direction = sanitize_component(record.get("flight_direction") or record.get("flightDirection"))
		path_number = record.get("path_number") or record.get("pathNumber") or record.get("track") or record.get("relativeOrbit")
		beam_mode = sanitize_component(record.get("beam_mode") or record.get("beamModeType") or "NA", fallback="NA")
		return "|".join((dataset_name, platform, flight_direction, str(path_number or "NA"), beam_mode))
	if dataset_name == "BIOMASS":
		mode = sanitize_component(record.get("mode") or _parse_biomass_part(record, "mode") or "NA", fallback="NA")
		orbit_state = sanitize_component(record.get("orbit_state") or record.get("orbitState") or record.get("flight_direction") or "UNKNOWN")
		track_number = record.get("track_number") or _parse_biomass_numeric(record, prefix="T")
		frame_number = record.get("frame_number") or _parse_biomass_numeric(record, prefix="F")
		return "|".join((dataset_name, mode, orbit_state, str(track_number or "NA"), str(frame_number or "NA")))
	raise ValueError(f"Unsupported dataset for orbit key derivation: {dataset}")


def build_product_uid(dataset: str, record: dict[str, Any]) -> str:
	provider_product_id = record.get("provider_product_id") or record.get("product_id") or record.get("item_id")
	if provider_product_id in (None, ""):
		raise ValueError("Cannot build product UID without provider_product_id or item_id.")
	return f"{dataset.strip().upper()}|{provider_product_id}"


def build_asset_uid(product_uid: str, asset_key: str) -> str:
	return f"{product_uid}|{sanitize_component(asset_key, fallback='asset')}"


def build_predicted_event_id(dataset: str, aoi_id: str, orbit_scope_key: str, predicted_acquisition_utc: str, policy_id: str) -> str:
	return _hash_identity(dataset, aoi_id, orbit_scope_key, predicted_acquisition_utc, policy_id)


def build_query_window_id(dataset: str, aoi_id: str, orbit_scope_key: str, window_start_utc: str, window_end_utc: str, window_role: str) -> str:
	return _hash_identity(dataset, aoi_id, orbit_scope_key, window_start_utc, window_end_utc, window_role)


def _hash_identity(*parts: str) -> str:
	joined = "||".join(parts)
	return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _parse_biomass_part(record: dict[str, Any], expected: str) -> str | None:
	scene_name = record.get("scene_name") or record.get("sceneName") or record.get("title")
	if not isinstance(scene_name, str):
		return None
	parts = [part for part in scene_name.split("_") if part]
	if expected == "mode" and len(parts) > 2:
		return parts[2]
	return None


def _parse_biomass_numeric(record: dict[str, Any], prefix: str) -> int | None:
	scene_name = record.get("scene_name") or record.get("sceneName") or record.get("title")
	if not isinstance(scene_name, str):
		return None
	for part in scene_name.split("_"):
		if part.startswith(prefix) and part[len(prefix):].isdigit():
			return int(part[len(prefix):])
	return None