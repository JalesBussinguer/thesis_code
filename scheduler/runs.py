from __future__ import annotations

import sqlite3
import uuid

from .db import current_utc
from .models import SCHEDULER_VERSION


def create_run(connection: sqlite3.Connection, mode: str, trigger_type: str = "manual") -> str:
	run_id = f"run-{uuid.uuid4()}"
	now_utc = current_utc()
	db_mode = mode.replace("-", "_")
	connection.execute(
		"""
		INSERT INTO runs (
			run_id, trigger_type, mode, host, pid, started_at_utc, ended_at_utc,
			status, exit_code, scheduler_version, dry_run, config_hash, notes,
			created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(
			run_id,
			trigger_type,
			db_mode,
			"localhost",
			None,
			now_utc,
			None,
			"running",
			None,
			SCHEDULER_VERSION,
			1 if mode == "dry-run" else 0,
			"phase0",
			None,
			now_utc,
			now_utc,
		),
	)
	connection.commit()
	return run_id


def finalize_run(connection: sqlite3.Connection, run_id: str, status: str, exit_code: int, notes: str | None = None) -> None:
	now_utc = current_utc()
	connection.execute(
		"""
		UPDATE runs
		SET ended_at_utc = ?, status = ?, exit_code = ?, notes = ?, updated_at_utc = ?
		WHERE run_id = ?
		""",
		(now_utc, status, exit_code, notes, now_utc, run_id),
	)
	connection.commit()


def open_step(connection: sqlite3.Connection, run_id: str, step_name: str) -> str:
	step_id = f"step-{uuid.uuid4()}"
	now_utc = current_utc()
	connection.execute(
		"""
		INSERT INTO run_steps (
			run_step_id, run_id, step_name, started_at_utc, ended_at_utc, status,
			checkpoint_token, records_in, records_out, error_class, error_message,
			created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(step_id, run_id, step_name, now_utc, None, "running", None, None, None, None, None, now_utc, now_utc),
	)
	connection.commit()
	return step_id


def close_step_success(connection: sqlite3.Connection, run_step_id: str, records_in: int = 0, records_out: int = 0) -> None:
	now_utc = current_utc()
	connection.execute(
		"""
		UPDATE run_steps
		SET ended_at_utc = ?, status = 'succeeded', records_in = ?, records_out = ?, updated_at_utc = ?
		WHERE run_step_id = ?
		""",
		(now_utc, records_in, records_out, now_utc, run_step_id),
	)
	connection.commit()


def close_step_failure(connection: sqlite3.Connection, run_step_id: str, error: Exception) -> None:
	now_utc = current_utc()
	connection.execute(
		"""
		UPDATE run_steps
		SET ended_at_utc = ?, status = 'failed', error_class = ?, error_message = ?, updated_at_utc = ?
		WHERE run_step_id = ?
		""",
		(now_utc, error.__class__.__name__, str(error), now_utc, run_step_id),
	)
	connection.commit()