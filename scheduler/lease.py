from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from .db import current_utc
from .models import DEFAULT_LEASE_TTL_SECONDS, MAIN_LEASE_NAME


class LeaseHeldError(RuntimeError):
	"""Raised when another scheduler execution holds the main lease."""


def acquire_main_lease(connection: sqlite3.Connection, run_id: str, ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS) -> None:
	now = datetime.now(UTC)
	now_utc = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
	expires_at_utc = (now + timedelta(seconds=ttl_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
	row = connection.execute(
		"SELECT owner_run_id, expires_at_utc FROM run_lease WHERE lease_name = ?",
		(MAIN_LEASE_NAME,),
	).fetchone()
	if row is None:
		connection.execute(
			"INSERT INTO run_lease (lease_name, owner_run_id, acquired_at_utc, heartbeat_at_utc, expires_at_utc) VALUES (?, ?, ?, ?, ?)",
			(MAIN_LEASE_NAME, run_id, now_utc, now_utc, expires_at_utc),
		)
		connection.commit()
		return
	expires_at = datetime.fromisoformat(row["expires_at_utc"].replace("Z", "+00:00")).astimezone(UTC)
	if expires_at <= now:
		connection.execute(
			"""
			UPDATE run_lease
			SET owner_run_id = ?, acquired_at_utc = ?, heartbeat_at_utc = ?, expires_at_utc = ?
			WHERE lease_name = ?
			""",
			(run_id, now_utc, now_utc, expires_at_utc, MAIN_LEASE_NAME),
		)
		connection.commit()
		return
	raise LeaseHeldError("Another scheduler run currently holds the main lease")


def release_main_lease(connection: sqlite3.Connection, run_id: str) -> None:
	connection.execute(
		"DELETE FROM run_lease WHERE lease_name = ? AND owner_run_id = ?",
		(MAIN_LEASE_NAME, run_id),
	)
	connection.commit()


def seed_active_lease(connection: sqlite3.Connection, run_id: str, ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS) -> None:
	now = current_utc()
	expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
	connection.execute(
		"INSERT OR REPLACE INTO run_lease (lease_name, owner_run_id, acquired_at_utc, heartbeat_at_utc, expires_at_utc) VALUES (?, ?, ?, ?, ?)",
		(MAIN_LEASE_NAME, run_id, now, now, expires_at),
	)
	connection.commit()