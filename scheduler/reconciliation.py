from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from .db import current_utc
from .models import AssetRecord, ProductRecord
from .orbit_keys import build_asset_uid, build_product_uid


def upsert_product(
	connection: sqlite3.Connection,
	aoi_id: str,
	product: ProductRecord,
	intersects_aoi: bool | None = None,
	intersection_fraction: float | None = None,
	coverage_id: str | None = None,
) -> str:
	now_utc = current_utc()
	product_uid = build_product_uid(product.dataset, {"provider_product_id": product.provider_product_id})
	initial_status = "eligible" if intersects_aoi else "discovered"
	connection.execute(
		"""
		INSERT INTO products (
			product_uid, dataset, aoi_id, provider_product_id, scene_name, item_id,
			platform, processing_level, orbit_scope_key, relative_orbit,
			absolute_orbit, path_number, frame_number, beam_mode, flight_direction,
			acquisition_start_utc, acquisition_stop_utc, first_detected_at_utc,
			last_detected_at_utc, first_query_window_id, intersects_aoi,
			intersection_fraction, coverage_id, current_status, metadata_json,
			created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(dataset, provider_product_id) DO UPDATE SET
			aoi_id = excluded.aoi_id,
			scene_name = COALESCE(excluded.scene_name, products.scene_name),
			item_id = COALESCE(excluded.item_id, products.item_id),
			platform = COALESCE(excluded.platform, products.platform),
			processing_level = COALESCE(excluded.processing_level, products.processing_level),
			orbit_scope_key = excluded.orbit_scope_key,
			relative_orbit = COALESCE(excluded.relative_orbit, products.relative_orbit),
			absolute_orbit = COALESCE(excluded.absolute_orbit, products.absolute_orbit),
			path_number = COALESCE(excluded.path_number, products.path_number),
			frame_number = COALESCE(excluded.frame_number, products.frame_number),
			beam_mode = COALESCE(excluded.beam_mode, products.beam_mode),
			flight_direction = COALESCE(excluded.flight_direction, products.flight_direction),
			acquisition_start_utc = COALESCE(excluded.acquisition_start_utc, products.acquisition_start_utc),
			acquisition_stop_utc = COALESCE(excluded.acquisition_stop_utc, products.acquisition_stop_utc),
			last_detected_at_utc = excluded.last_detected_at_utc,
			intersects_aoi = COALESCE(excluded.intersects_aoi, products.intersects_aoi),
			intersection_fraction = COALESCE(excluded.intersection_fraction, products.intersection_fraction),
			coverage_id = COALESCE(excluded.coverage_id, products.coverage_id),
			current_status = CASE
				WHEN excluded.intersects_aoi = 1 THEN 'eligible'
				ELSE products.current_status
			END,
			metadata_json = excluded.metadata_json,
			updated_at_utc = excluded.updated_at_utc
		""",
		(
			product_uid,
			product.dataset,
			aoi_id,
			product.provider_product_id,
			product.scene_name,
			product.item_id,
			product.platform,
			product.processing_level,
			product.orbit_scope_key,
			product.relative_orbit,
			product.absolute_orbit,
			product.path_number,
			product.frame_number,
			product.beam_mode,
			product.flight_direction,
			product.acquisition_start_utc,
			product.acquisition_stop_utc,
			now_utc,
			now_utc,
			None,
			None if intersects_aoi is None else (1 if intersects_aoi else 0),
			intersection_fraction,
			coverage_id,
			initial_status,
			json.dumps(product.metadata_json, ensure_ascii=True),
			now_utc,
			now_utc,
		),
	)
	connection.commit()
	return product_uid


def upsert_product_assets(connection: sqlite3.Connection, product_uid: str, assets: Iterable[AssetRecord]) -> None:
	now_utc = current_utc()
	for asset in assets:
		asset_uid = build_asset_uid(product_uid, asset.asset_key)
		connection.execute(
			"""
			INSERT INTO product_assets (
				asset_uid, product_uid, asset_key, source_url, filename, size_mb,
				checksum_hint, local_path, asset_status, first_detected_at_utc,
				first_download_attempt_at_utc, last_download_attempt_at_utc,
				completed_at_utc, integrity_status, integrity_notes,
				created_at_utc, updated_at_utc
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			ON CONFLICT(product_uid, asset_key) DO UPDATE SET
				source_url = excluded.source_url,
				filename = excluded.filename,
				size_mb = COALESCE(excluded.size_mb, product_assets.size_mb),
				checksum_hint = COALESCE(excluded.checksum_hint, product_assets.checksum_hint),
				updated_at_utc = excluded.updated_at_utc
			""",
			(
				asset_uid,
				product_uid,
				asset.asset_key,
				asset.source_url,
				asset.filename,
				asset.size_mb,
				asset.checksum_hint,
				None,
				"discovered",
				now_utc,
				None,
				None,
				None,
				"unchecked",
				None,
				now_utc,
				now_utc,
			),
		)
	connection.commit()