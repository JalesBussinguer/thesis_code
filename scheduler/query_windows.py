from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from .db import current_utc
from .orbit_keys import build_query_window_id


def plan_discovery_window(
	connection: sqlite3.Connection,
	dataset: str,
	aoi_id: str,
	orbit_scope_key: str,
	query_margin_hours: int,
	reason: str = "scheduled_poll",
	now_utc: str | None = None,
) -> str:
	now = _parse_utc(now_utc or current_utc())
	window_end = now.replace(microsecond=0)
	window_start = (window_end - timedelta(hours=query_margin_hours)).replace(microsecond=0)
	window_start_text = _format_utc(window_start)
	window_end_text = _format_utc(window_end)
	query_window_id = build_query_window_id(
		dataset=dataset.upper(),
		aoi_id=aoi_id,
		orbit_scope_key=orbit_scope_key,
		window_start_utc=window_start_text,
		window_end_utc=window_end_text,
		window_role="discovery_window",
	)
	planned_at_utc = _format_utc(window_end)
	connection.execute(
		"""
		INSERT INTO query_windows (
			query_window_id, dataset, aoi_id, orbit_scope_key, window_start_utc,
			window_end_utc, window_role, planned_at_utc, executed_in_run_id,
			status, retry_count, next_retry_at_utc, response_fingerprint,
			result_count, error_class, error_message, created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(dataset, aoi_id, orbit_scope_key, window_start_utc, window_end_utc, window_role)
		DO UPDATE SET planned_at_utc = excluded.planned_at_utc, updated_at_utc = excluded.updated_at_utc
		""",
		(
			query_window_id,
			dataset.upper(),
			aoi_id,
			orbit_scope_key,
			window_start_text,
			window_end_text,
			"discovery_window",
			planned_at_utc,
			None,
			"planned",
			0,
			None,
			None,
			None,
			None,
			None,
			planned_at_utc,
			planned_at_utc,
		),
	)
	connection.commit()
	materialize_poll_queue(
		connection,
		dataset=dataset,
		aoi_id=aoi_id,
		orbit_scope_key=orbit_scope_key,
		query_window_id=query_window_id,
		scheduled_for_utc=planned_at_utc,
		reason=reason,
	)
	return query_window_id


def materialize_poll_queue(
	connection: sqlite3.Connection,
	dataset: str,
	aoi_id: str,
	orbit_scope_key: str,
	query_window_id: str,
	scheduled_for_utc: str,
	reason: str,
	priority: int = 100,
) -> str:
	now_utc = current_utc()
	queue_item_id = f"queue-{uuid.uuid4()}"
	connection.execute(
		"""
		INSERT INTO poll_queue (
			queue_item_id, dataset, aoi_id, orbit_scope_key, predicted_event_id,
			query_window_id, scheduled_for_utc, queue_state, priority, reason,
			created_at_utc, claimed_by_run_id, claimed_at_utc, finished_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(query_window_id) DO UPDATE SET
			scheduled_for_utc = excluded.scheduled_for_utc,
			priority = excluded.priority,
			reason = excluded.reason,
			updated_at_utc = excluded.updated_at_utc
		""",
		(
			queue_item_id,
			dataset.upper(),
			aoi_id,
			orbit_scope_key,
			None,
			query_window_id,
			scheduled_for_utc,
			"pending",
			priority,
			reason,
			now_utc,
			None,
			None,
			None,
			now_utc,
		),
	)
	connection.commit()
	row = connection.execute("SELECT queue_item_id FROM poll_queue WHERE query_window_id = ?", (query_window_id,)).fetchone()
	return row["queue_item_id"]


def claim_poll_queue_item(connection: sqlite3.Connection, queue_item_id: str, run_id: str) -> None:
	now_utc = current_utc()
	connection.execute(
		"""
		UPDATE poll_queue
		SET queue_state = 'claimed', claimed_by_run_id = ?, claimed_at_utc = ?, updated_at_utc = ?
		WHERE queue_item_id = ?
		""",
		(run_id, now_utc, now_utc, queue_item_id),
	)
	connection.commit()


def complete_poll_queue_item(connection: sqlite3.Connection, queue_item_id: str, state: str = "completed") -> None:
	now_utc = current_utc()
	connection.execute(
		"""
		UPDATE poll_queue
		SET queue_state = ?, finished_at_utc = ?, updated_at_utc = ?
		WHERE queue_item_id = ?
		""",
		(state, now_utc, now_utc, queue_item_id),
	)
	connection.commit()


def mark_query_window_executed(
	connection: sqlite3.Connection,
	query_window_id: str,
	run_id: str,
	status: str,
	result_count: int,
	response_fingerprint: str,
	error_class: str | None = None,
	error_message: str | None = None,
) -> None:
	now_utc = current_utc()
	connection.execute(
		"""
		UPDATE query_windows
		SET executed_in_run_id = ?, status = ?, result_count = ?, response_fingerprint = ?,
			error_class = ?, error_message = ?, updated_at_utc = ?
		WHERE query_window_id = ?
		""",
		(run_id, status, result_count, response_fingerprint, error_class, error_message, now_utc, query_window_id),
	)
	connection.commit()


def _parse_utc(value: str) -> datetime:
	return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _format_utc(value: datetime) -> str:
	return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")