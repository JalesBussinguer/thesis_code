from __future__ import annotations

import sqlite3
import uuid

from .db import current_utc
from .models import AllowlistDecision, IntersectionResult


def classify_allowlist_decision(
	dataset: str,
	intersection: IntersectionResult,
	observation_count: int = 1,
	response_quality: str = "ok",
) -> AllowlistDecision:
	dataset_name = dataset.upper()
	if not intersection.intersects or intersection.intersection_area_km2 <= 0:
		return AllowlistDecision("outside", "blocked", "outside_aoi", False)
	if response_quality == "ambiguous":
		return AllowlistDecision("candidate_new", "candidate", "new_track_candidate", False)
	if dataset_name in {"NISAR", "BIOMASS"}:
		return AllowlistDecision("intersects", "allowed", "intersects_confirmed", True)
	if intersection.intersection_fraction >= 0.05 or observation_count >= 2:
		return AllowlistDecision("intersects", "allowed", "intersects_confirmed", True)
	return AllowlistDecision("candidate_new", "candidate", "new_track_candidate", False)


def upsert_orbit_aoi_coverage(
	connection: sqlite3.Connection,
	orbit_scope_key: str,
	aoi_id: str,
	intersection: IntersectionResult,
	decision: AllowlistDecision,
	run_id: str | None = None,
) -> str:
	now_utc = current_utc()
	row = connection.execute(
		"SELECT coverage_id, first_confirmed_at_utc FROM orbit_aoi_coverage WHERE orbit_scope_key = ? AND aoi_id = ?",
		(orbit_scope_key, aoi_id),
	).fetchone()
	coverage_id = row["coverage_id"] if row else f"coverage-{uuid.uuid4()}"
	first_confirmed = row["first_confirmed_at_utc"] if row else None
	if decision.coverage_status == "intersects" and not first_confirmed:
		first_confirmed = now_utc
	last_confirmed = now_utc if decision.coverage_status == "intersects" else None
	connection.execute(
		"""
		INSERT INTO orbit_aoi_coverage (
			coverage_id, orbit_scope_key, aoi_id, coverage_status, intersection_area_km2,
			intersection_fraction, footprint_count_used, first_confirmed_at_utc,
			last_confirmed_at_utc, last_checked_at_utc, source_run_id,
			geometry_evidence_path, notes, created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(orbit_scope_key, aoi_id) DO UPDATE SET
			coverage_status = excluded.coverage_status,
			intersection_area_km2 = excluded.intersection_area_km2,
			intersection_fraction = excluded.intersection_fraction,
			footprint_count_used = excluded.footprint_count_used,
			first_confirmed_at_utc = COALESCE(orbit_aoi_coverage.first_confirmed_at_utc, excluded.first_confirmed_at_utc),
			last_confirmed_at_utc = excluded.last_confirmed_at_utc,
			last_checked_at_utc = excluded.last_checked_at_utc,
			source_run_id = excluded.source_run_id,
			updated_at_utc = excluded.updated_at_utc
		""",
		(
			coverage_id,
			orbit_scope_key,
			aoi_id,
			decision.coverage_status,
			intersection.intersection_area_km2,
			intersection.intersection_fraction,
			1,
			first_confirmed,
			last_confirmed,
			now_utc,
			run_id,
			None,
			None,
			now_utc,
			now_utc,
		),
	)
	connection.commit()
	return coverage_id


def upsert_allowlist_entry(
	connection: sqlite3.Connection,
	aoi_id: str,
	orbit_scope_key: str,
	decision: AllowlistDecision,
	coverage_id: str,
	run_id: str | None = None,
) -> str:
	now_utc = current_utc()
	row = connection.execute(
		"SELECT allowlist_id, first_added_at_utc FROM orbit_download_allowlist WHERE aoi_id = ? AND orbit_scope_key = ?",
		(aoi_id, orbit_scope_key),
	).fetchone()
	allowlist_id = row["allowlist_id"] if row else f"allow-{uuid.uuid4()}"
	first_added = row["first_added_at_utc"] if row else now_utc
	connection.execute(
		"""
		INSERT INTO orbit_download_allowlist (
			allowlist_id, aoi_id, orbit_scope_key, allow_status, allow_reason,
			auto_discovered, first_added_at_utc, last_reviewed_at_utc,
			source_coverage_id, source_run_id, notes, created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(aoi_id, orbit_scope_key) DO UPDATE SET
			allow_status = excluded.allow_status,
			allow_reason = excluded.allow_reason,
			auto_discovered = excluded.auto_discovered,
			last_reviewed_at_utc = excluded.last_reviewed_at_utc,
			source_coverage_id = excluded.source_coverage_id,
			source_run_id = excluded.source_run_id,
			updated_at_utc = excluded.updated_at_utc
		""",
		(
			allowlist_id,
			aoi_id,
			orbit_scope_key,
			decision.allow_status,
			decision.allow_reason,
			1 if decision.auto_discovered else 0,
			first_added,
			now_utc,
			coverage_id,
			run_id,
			None,
			now_utc,
			now_utc,
		),
	)
	connection.commit()
	return allowlist_id