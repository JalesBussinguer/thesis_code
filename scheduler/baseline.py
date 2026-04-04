from __future__ import annotations

import sqlite3
from typing import Any

from .db import current_utc


def upsert_orbit_baseline(connection: sqlite3.Connection, orbit_scope_key: str, record: dict[str, Any]) -> None:
	now_utc = current_utc()
	dataset = str(record["dataset"]).upper()
	platform = record.get("platform")
	flight_direction = record.get("flight_direction") or record.get("flightDirection")
	path_number = record.get("path_number")
	frame_number = record.get("frame_number")
	beam_mode = record.get("beam_mode")
	mode = record.get("mode")
	orbit_state = record.get("orbit_state")
	track_number = record.get("track_number")
	frame_code = record.get("frame_code")
	acquisition_start = record.get("acquisition_start_utc")
	acquisition_stop = record.get("acquisition_stop_utc")
	connection.execute(
		"""
		INSERT INTO orbit_baseline (
			orbit_scope_key, dataset, platform, flight_direction, path_number,
			frame_number, beam_mode, mode, orbit_state, track_number, frame_code,
			first_seen_acquisition_utc, last_seen_acquisition_utc,
			median_gap_hours, p90_gap_hours, historical_scene_count,
			confidence_score, last_calibrated_at_utc, baseline_source,
			created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(orbit_scope_key) DO UPDATE SET
			platform = excluded.platform,
			flight_direction = excluded.flight_direction,
			path_number = excluded.path_number,
			frame_number = COALESCE(excluded.frame_number, orbit_baseline.frame_number),
			beam_mode = COALESCE(excluded.beam_mode, orbit_baseline.beam_mode),
			mode = COALESCE(excluded.mode, orbit_baseline.mode),
			orbit_state = COALESCE(excluded.orbit_state, orbit_baseline.orbit_state),
			track_number = COALESCE(excluded.track_number, orbit_baseline.track_number),
			frame_code = COALESCE(excluded.frame_code, orbit_baseline.frame_code),
			first_seen_acquisition_utc = COALESCE(orbit_baseline.first_seen_acquisition_utc, excluded.first_seen_acquisition_utc),
			last_seen_acquisition_utc = COALESCE(excluded.last_seen_acquisition_utc, orbit_baseline.last_seen_acquisition_utc),
			historical_scene_count = orbit_baseline.historical_scene_count + 1,
			last_calibrated_at_utc = excluded.last_calibrated_at_utc,
			updated_at_utc = excluded.updated_at_utc
		""",
		(
			orbit_scope_key,
			dataset,
			platform,
			flight_direction,
			path_number,
			frame_number,
			beam_mode,
			mode,
			orbit_state,
			track_number,
			frame_code,
			acquisition_start,
			acquisition_stop,
			None,
			None,
			1,
			0.25,
			now_utc,
			"live_inventory",
			now_utc,
			now_utc,
		),
	)
	connection.commit()


def fetch_orbit_baseline(connection: sqlite3.Connection, orbit_scope_key: str) -> sqlite3.Row | None:
	return connection.execute(
		"SELECT * FROM orbit_baseline WHERE orbit_scope_key = ?",
		(orbit_scope_key,),
	).fetchone()