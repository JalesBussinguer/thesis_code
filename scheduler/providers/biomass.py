from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Iterable

from pystac_client import Client

from ..models import AssetRecord, ProductRecord, ProviderDiscoveryContext, SearchArea
from ..orbit_keys import build_orbit_scope_key


CATALOG_URL = "https://catalog.maap.eo.esa.int/catalogue/"
DEFAULT_COLLECTION = "BiomassLevel1a"
DEFAULT_PRODUCT_TYPE_BY_COLLECTION = {
	"BiomassLevel1a": "S1_SCS__1S",
	"BiomassLevel1b": "S1_DGM__1S",
}


def build_search_kwargs(
	context: ProviderDiscoveryContext,
	orbit_row: Any,
	search_area: SearchArea,
	collection: str = DEFAULT_COLLECTION,
	max_items: int | None = None,
) -> dict[str, Any]:
	product_type = DEFAULT_PRODUCT_TYPE_BY_COLLECTION.get(collection)
	query: dict[str, Any] = {
		"collections": [collection],
		"intersects": search_area.geometry_geojson,
		"datetime": f"{context.window_start_utc}/{context.window_end_utc}",
		"method": "POST",
		"max_items": max_items,
	}
	if product_type:
		query["filter"] = f"(product:type='{product_type}')"
	return query


def discover_items(
	context: ProviderDiscoveryContext,
	orbit_row: Any,
	search_callable: Callable[..., Iterable[Any]] | None = None,
	collection: str = DEFAULT_COLLECTION,
	max_items: int | None = None,
) -> tuple[list[Any], str]:
	search_callable = search_callable or _default_search_callable
	items: list[Any] = []
	seen_ids: set[str] = set()
	fingerprints: list[str] = []
	for search_area in context.search_areas:
		kwargs = build_search_kwargs(context, orbit_row, search_area, collection=collection, max_items=max_items)
		fingerprints.append(json.dumps(kwargs, sort_keys=True, ensure_ascii=True))
		for item in search_callable(**kwargs):
			item_id = _item_id(item)
			if not item_id or item_id in seen_ids:
				continue
			if not _matches_biomass_orbit(item, orbit_row):
				continue
			seen_ids.add(item_id)
			items.append(item)
	return items, _fingerprint_payload(fingerprints)


def item_to_product(item: Any, dataset: str = "BIOMASS") -> ProductRecord:
	properties = _item_properties(item)
	product_name = str(properties.get("title") or _item_id(item))
	orbit_fields = parse_orbit_fields(product_name)
	orbit_state = _normalize_text(properties.get("sat:orbit_state"))
	mapped = {
		"mode": orbit_fields["mode"],
		"orbit_state": orbit_state.upper() if orbit_state else "UNKNOWN",
		"track_number": orbit_fields["track_number"],
		"frame_number": orbit_fields["frame_number"],
	}
	orbit_scope_key = build_orbit_scope_key(dataset, mapped)
	start_time = _normalize_text(properties.get("start_datetime") or properties.get("datetime"))
	end_time = _normalize_text(properties.get("end_datetime") or properties.get("datetime"))
	footprint_wkt = _geometry_wkt(item)
	return ProductRecord(
		dataset=dataset,
		provider_product_id=_item_id(item),
		item_id=_item_id(item),
		scene_name=product_name,
		platform="BIOMASS",
		processing_level=_item_collection(item),
		orbit_scope_key=orbit_scope_key,
		absolute_orbit=_as_int(properties.get("sat:absolute_orbit")),
		mode=orbit_fields["mode"],
		orbit_state=orbit_state.upper() if orbit_state else None,
		track_number=orbit_fields["track_number"],
		frame_number=orbit_fields["frame_number"],
		frame_code=orbit_fields["frame"],
		flight_direction=orbit_state.upper() if orbit_state else None,
		acquisition_start_utc=start_time,
		acquisition_stop_utc=end_time,
		footprint_wkt=footprint_wkt,
		metadata_json={"raw_properties": properties},
		assets=tuple(_item_assets(item)),
	)


def parse_orbit_fields(product_name: str) -> dict[str, Any]:
	parts = [part for part in product_name.split("_") if part]
	if len(parts) <= 11:
		raise ValueError(f"Invalid BIOMASS product name for orbit parsing: {product_name}")
	mode = parts[2]
	track = parts[10]
	frame = parts[11]
	track_number = int(track.removeprefix("T")) if track.startswith("T") and track[1:].isdigit() else None
	frame_number = int(frame.removeprefix("F")) if frame.startswith("F") and frame[1:].isdigit() else None
	return {
		"mode": mode,
		"track": track,
		"frame": frame,
		"track_number": track_number,
		"frame_number": frame_number,
	}


def _matches_biomass_orbit(item: Any, orbit_row: Any) -> bool:
	product_name = str(_item_properties(item).get("title") or _item_id(item))
	fields = parse_orbit_fields(product_name)
	if orbit_row["track_number"] is not None and fields["track_number"] != orbit_row["track_number"]:
		return False
	if orbit_row["frame_number"] is not None and fields["frame_number"] != orbit_row["frame_number"]:
		return False
	if orbit_row["mode"] and fields["mode"] != orbit_row["mode"]:
		return False
	return True


def _item_properties(item: Any) -> dict[str, Any]:
	if hasattr(item, "properties"):
		return dict(item.properties)
	if isinstance(item, dict):
		return dict(item.get("properties", {}))
	raise TypeError("Unsupported BIOMASS item type.")


def _item_id(item: Any) -> str:
	if hasattr(item, "id"):
		return str(item.id)
	if isinstance(item, dict):
		return str(item.get("id") or item.get("item_id"))
	raise TypeError("Unsupported BIOMASS item type.")


def _item_collection(item: Any) -> str | None:
	if hasattr(item, "collection_id"):
		return item.collection_id
	if isinstance(item, dict):
		return item.get("collection")
	return None


def _geometry_wkt(item: Any) -> str | None:
	from shapely.geometry import shape
	from shapely.wkt import dumps as dump_wkt

	geometry = item.geometry if hasattr(item, "geometry") else item.get("geometry")
	if geometry is None:
		return None
	return dump_wkt(shape(geometry))


def _item_assets(item: Any) -> list[AssetRecord]:
	assets = item.assets if hasattr(item, "assets") else item.get("assets", {})
	records: list[AssetRecord] = []
	for asset_key, asset in assets.items():
		href = getattr(asset, "href", None) if not isinstance(asset, dict) else asset.get("href")
		if not isinstance(href, str) or not href:
			continue
		records.append(
			AssetRecord(
				asset_key=str(asset_key),
				source_url=href,
				filename=href.rstrip("/").rsplit("/", 1)[-1] or f"{asset_key}.bin",
				asset_type=str(asset_key),
				is_required=(asset_key == "product"),
			)
		)
	return records


def _normalize_text(value: Any) -> str | None:
	if value in (None, ""):
		return None
	if isinstance(value, list):
		return ",".join(str(item) for item in value)
	return str(value)


def _as_int(value: Any) -> int | None:
	if value in (None, ""):
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _fingerprint_payload(parts: list[str]) -> str:
	joined = "||".join(parts)
	return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _default_search_callable(**kwargs) -> Iterable[Any]:
	catalog = Client.open(CATALOG_URL)
	search = catalog.search(**kwargs)
	return search.items()