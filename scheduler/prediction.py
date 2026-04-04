from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from .db import current_utc
from .orbit_keys import build_predicted_event_id


def ensure_predicted_event(
	connection: sqlite3.Connection,
	orbit_row: sqlite3.Row,
	policy: sqlite3.Row,
	now_utc: str | None = None,
) -> sqlite3.Row:
	last_seen_utc = orbit_row["last_seen_acquisition_utc"] or orbit_row["first_seen_acquisition_utc"]
	if not last_seen_utc:
		raise ValueError(f"Orbit baseline has no acquisition timestamps: {orbit_row['orbit_scope_key']}")
	now = _parse_utc(now_utc or current_utc())
	last_seen = _parse_utc(last_seen_utc)
	gap_hours = _predict_gap_hours(orbit_row, policy)
	predicted_acquisition = last_seen + timedelta(hours=gap_hours)
	availability_start = predicted_acquisition - timedelta(hours=int(policy["pre_window_hours"]))
	availability_end = predicted_acquisition + timedelta(hours=int(policy["active_window_hours"]))
	predicted_event_id = build_predicted_event_id(
		dataset=orbit_row["dataset"],
		aoi_id=orbit_row["aoi_id"],
		orbit_scope_key=orbit_row["orbit_scope_key"],
		predicted_acquisition_utc=_format_utc(predicted_acquisition),
		policy_id=policy["policy_id"],
	)
	status = _derive_status(now, availability_start, availability_end)
	now_text = _format_utc(now)
	connection.execute(
		"""
		INSERT INTO predicted_events (
			predicted_event_id, dataset, aoi_id, orbit_scope_key, policy_id,
			predicted_acquisition_utc, availability_start_utc, availability_end_utc,
			confidence_score, uncertainty_hours, historical_gap_hours,
			derived_from_baseline_at_utc, status, superseded_by_event_id,
			last_evaluated_at_utc, created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(dataset, aoi_id, orbit_scope_key, predicted_acquisition_utc, policy_id)
		DO UPDATE SET
			confidence_score = excluded.confidence_score,
			uncertainty_hours = excluded.uncertainty_hours,
			historical_gap_hours = excluded.historical_gap_hours,
			derived_from_baseline_at_utc = excluded.derived_from_baseline_at_utc,
			status = CASE
				WHEN predicted_events.status IN ('satisfied', 'superseded') THEN predicted_events.status
				ELSE excluded.status
			END,
			last_evaluated_at_utc = excluded.last_evaluated_at_utc,
			updated_at_utc = excluded.updated_at_utc
		""",
		(
			predicted_event_id,
			orbit_row["dataset"],
			orbit_row["aoi_id"],
			orbit_row["orbit_scope_key"],
			policy["policy_id"],
			_format_utc(predicted_acquisition),
			_format_utc(availability_start),
			_format_utc(availability_end),
			float(orbit_row["confidence_score"] or policy["min_confidence"]),
			float(max(int(policy["pre_window_hours"]), int(policy["query_margin_hours"]))),
			float(gap_hours),
			orbit_row["last_calibrated_at_utc"] or last_seen_utc,
			status,
			None,
			now_text,
			now_text,
			now_text,
		),
	)
	connection.commit()
	return connection.execute(
		"SELECT * FROM predicted_events WHERE predicted_event_id = ?",
		(predicted_event_id,),
	).fetchone()


def update_predicted_event_after_query(
	connection: sqlite3.Connection,
	predicted_event_id: str,
	result_count: int,
	now_utc: str | None = None,
) -> str:
	row = connection.execute(
		"SELECT availability_start_utc, availability_end_utc, status FROM predicted_events WHERE predicted_event_id = ?",
		(predicted_event_id,),
	).fetchone()
	if row is None:
		raise ValueError(f"Predicted event not found: {predicted_event_id}")
	now = _parse_utc(now_utc or current_utc())
	availability_start = _parse_utc(row["availability_start_utc"])
	availability_end = _parse_utc(row["availability_end_utc"])
	if result_count > 0:
		status = "satisfied"
	elif now > availability_end:
		status = "missed"
	elif now >= availability_start:
		status = "active"
	else:
		status = "predicted"
	connection.execute(
		"UPDATE predicted_events SET status = ?, last_evaluated_at_utc = ?, updated_at_utc = ? WHERE predicted_event_id = ?",
		(status, _format_utc(now), _format_utc(now), predicted_event_id),
	)
	connection.commit()
	return status


def refresh_predicted_event_states(connection: sqlite3.Connection, now_utc: str | None = None) -> dict[str, int]:
	now = _parse_utc(now_utc or current_utc())
	rows = connection.execute(
		"SELECT predicted_event_id, availability_start_utc, availability_end_utc, status FROM predicted_events WHERE status NOT IN ('satisfied', 'superseded', 'stale')"
	).fetchall()
	counts = {"predicted": 0, "active": 0, "missed": 0}
	for row in rows:
		availability_start = _parse_utc(row["availability_start_utc"])
		availability_end = _parse_utc(row["availability_end_utc"])
		status = _derive_status(now, availability_start, availability_end)
		connection.execute(
			"UPDATE predicted_events SET status = ?, last_evaluated_at_utc = ?, updated_at_utc = ? WHERE predicted_event_id = ?",
			(status, _format_utc(now), _format_utc(now), row["predicted_event_id"]),
		)
		counts[status] = counts.get(status, 0) + 1
	connection.commit()
	return counts


def _predict_gap_hours(orbit_row: sqlite3.Row, policy: sqlite3.Row) -> float:
	for key in ("median_gap_hours", "p90_gap_hours"):
		value = orbit_row[key]
		if value not in (None, ""):
			return float(value)
	return float(policy["query_margin_hours"])


def _derive_status(now: datetime, availability_start: datetime, availability_end: datetime) -> str:
	if now > availability_end:
		return "missed"
	if now >= availability_start:
		return "active"
	return "predicted"


def _parse_utc(value: str) -> datetime:
	return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _format_utc(value: datetime) -> str:
	return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")