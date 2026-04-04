from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Callable

from shapely.wkt import loads as load_wkt

from .allowlist import classify_allowlist_decision, upsert_allowlist_entry, upsert_orbit_aoi_coverage
from .baseline import upsert_orbit_baseline
from .db import current_utc
from .downloads import queue_product_assets
from .geometry_aoi import compute_intersection_metrics
from .policies import get_enabled_policy
from .providers import build_discovery_context
from .providers.biomass import discover_items, item_to_product
from .providers.asf import discover_records, record_to_product
from .query_windows import claim_poll_queue_item, complete_poll_queue_item, mark_query_window_executed, plan_discovery_window
from .reconciliation import upsert_product, upsert_product_assets


SUPPORTED_POLL_DATASETS = {"SENTINEL-1", "NISAR", "BIOMASS"}


def list_poll_candidates(connection: sqlite3.Connection, datasets: set[str] | None = None) -> list[sqlite3.Row]:
	rows = connection.execute(
		"""
		SELECT
			ob.orbit_scope_key,
			ob.dataset,
			ob.platform,
			ob.flight_direction,
			ob.path_number,
			ob.frame_number,
			ob.beam_mode,
			ob.mode,
			ob.orbit_state,
			ob.track_number,
			ob.frame_code,
			oda.aoi_id,
			dp.policy_id,
			dp.query_margin_hours,
			CASE
				WHEN ob.dataset = 'BIOMASS' THEN NULL
				ELSE COALESCE(NULLIF(ob.mode, ''), NULL)
			END AS processing_level
		FROM orbit_download_allowlist oda
		JOIN orbit_baseline ob ON ob.orbit_scope_key = oda.orbit_scope_key
		JOIN dataset_policy dp ON dp.dataset = ob.dataset AND dp.enabled = 1
		WHERE oda.allow_status = 'allowed'
		ORDER BY dp.priority ASC, ob.dataset ASC, ob.orbit_scope_key ASC
		"""
	).fetchall()
	if datasets is None:
		return rows
	allowed = {dataset.upper() for dataset in datasets}
	return [row for row in rows if row["dataset"].upper() in allowed]


def execute_poll_only(
	connection: sqlite3.Connection,
	config: Any,
	run_id: str,
	request_get: Callable[..., Any] | dict[str, Callable[..., Any]] | None = None,
) -> dict[str, int]:
	candidates = list_poll_candidates(connection, SUPPORTED_POLL_DATASETS)
	results = {
		"candidates": len(candidates),
		"query_windows": 0,
		"records_found": 0,
		"products_upserted": 0,
		"queue_items_completed": 0,
	}
	for orbit_row in candidates:
		policy = get_enabled_policy(connection, orbit_row["dataset"])
		if policy is None:
			continue
		query_window_id = plan_discovery_window(
			connection,
			dataset=orbit_row["dataset"],
			aoi_id=orbit_row["aoi_id"],
			orbit_scope_key=orbit_row["orbit_scope_key"],
			query_margin_hours=policy["query_margin_hours"],
		)
		results["query_windows"] += 1
		window_row = connection.execute(
			"SELECT * FROM query_windows WHERE query_window_id = ?",
			(query_window_id,),
		).fetchone()
		queue_row = connection.execute("SELECT queue_item_id FROM poll_queue WHERE query_window_id = ?", (query_window_id,)).fetchone()
		if queue_row is None:
			continue
		context = build_discovery_context(
			config=config,
			dataset=orbit_row["dataset"],
			window_start_utc=window_row["window_start_utc"],
			window_end_utc=window_row["window_end_utc"],
		)
		try:
			claim_poll_queue_item(connection, queue_row["queue_item_id"], run_id)
			provider_callable = _provider_callable_for_dataset(request_get, orbit_row["dataset"])
			records, response_fingerprint = _discover_dataset_records(context, orbit_row, request_get=provider_callable)
			status = "results_found" if records else "empty"
			mark_query_window_executed(connection, query_window_id, run_id, status, len(records), response_fingerprint)
			_insert_api_observation(connection, query_window_id, run_id, orbit_row["dataset"], response_fingerprint, len(records), "ok" if records else "empty")
			results["records_found"] += len(records)
			validation_geometry = load_wkt(context.validation_geometry_wkt)
			for record in records:
				product = _record_to_product(orbit_row["dataset"], record)
				if not product.footprint_wkt:
					continue
				upsert_orbit_baseline(connection, product.orbit_scope_key, {**product.__dict__, "dataset": product.dataset})
				intersection = compute_intersection_metrics(product.footprint_wkt, validation_geometry)
				decision = classify_allowlist_decision(product.dataset, intersection)
				coverage_id = upsert_orbit_aoi_coverage(connection, product.orbit_scope_key, orbit_row["aoi_id"], intersection, decision, run_id)
				upsert_allowlist_entry(connection, orbit_row["aoi_id"], product.orbit_scope_key, decision, coverage_id, run_id)
				product_uid = upsert_product(
					connection,
					orbit_row["aoi_id"],
					product,
					intersects_aoi=intersection.intersects,
					intersection_fraction=intersection.intersection_fraction,
					coverage_id=coverage_id,
				)
				upsert_product_assets(connection, product_uid, product.assets)
				queue_product_assets(connection, product_uid)
				results["products_upserted"] += 1
			complete_poll_queue_item(connection, queue_row["queue_item_id"], "completed")
			results["queue_items_completed"] += 1
		except Exception as error:
			response_fingerprint = _hash_text(json.dumps({"orbit_scope_key": orbit_row["orbit_scope_key"]}, ensure_ascii=True))
			mark_query_window_executed(connection, query_window_id, run_id, "failed", 0, response_fingerprint, error.__class__.__name__, str(error))
			_insert_api_observation(connection, query_window_id, run_id, orbit_row["dataset"], response_fingerprint, 0, "failed", str(error))
			complete_poll_queue_item(connection, queue_row["queue_item_id"], "retry_scheduled")
			raise
	return results


def _discover_dataset_records(context, orbit_row, request_get=None):
	dataset = orbit_row["dataset"].upper()
	if dataset in {"SENTINEL-1", "NISAR"}:
		return discover_records(context, orbit_row, request_get=request_get)
	if dataset == "BIOMASS":
		return discover_items(context, orbit_row, search_callable=request_get)
	raise ValueError(f"Unsupported dataset for polling: {dataset}")


def _record_to_product(dataset: str, record):
	if dataset.upper() == "BIOMASS":
		return item_to_product(record, dataset="BIOMASS")
	return record_to_product(dataset, record)


def _insert_api_observation(
	connection: sqlite3.Connection,
	query_window_id: str,
	run_id: str,
	dataset: str,
	request_fingerprint: str,
	parsed_record_count: int,
	observation_status: str,
	notes: str | None = None,
) -> None:
	now_utc = current_utc()
	connection.execute(
		"""
		INSERT INTO api_observations (
			observation_id, query_window_id, run_id, dataset, endpoint_name,
			request_fingerprint, observed_at_utc, http_status, parsed_record_count,
			observation_status, anomaly_flag, raw_payload_path, notes, created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(
			f"obs-{uuid.uuid4()}",
			query_window_id,
			run_id,
			dataset.upper(),
			"asf_search",
			request_fingerprint,
			now_utc,
			200 if observation_status in {"ok", "empty"} else None,
			parsed_record_count,
			observation_status,
			0,
			None,
			notes,
			now_utc,
			now_utc,
		),
	)
	connection.commit()


def _hash_text(value: str) -> str:
	import hashlib

	return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provider_callable_for_dataset(request_get, dataset: str):
	if isinstance(request_get, dict):
		return request_get.get(dataset.upper())
	return request_get