from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .models import AppConfig, BootstrapSummary
from .paths import ensure_state_directories


POLICY_ROWS = [
	(
		"SENTINEL-1_v1",
		"SENTINEL-1",
		1,
		1,
		10,
		"observational",
		24,
		72,
		json.dumps([12, 24, 48, 72]),
		240,
		4,
		12,
		1,
		0.65,
		"Historico mais denso; polling frequente e janela mais estreita.",
	),
	(
		"NISAR_v1",
		"NISAR",
		1,
		1,
		20,
		"observational",
		48,
		120,
		json.dumps([24, 48, 96, 168]),
		504,
		4,
		24,
		1,
		0.40,
		"Historico esparso; janela maior e mais tolerancia a atraso.",
	),
	(
		"BIOMASS_v1",
		"BIOMASS",
		1,
		1,
		30,
		"observational",
		72,
		168,
		json.dumps([24, 72, 168, 336]),
		840,
		4,
		24,
		1,
		0.35,
		"Sem calendario oficial; polling conservador e recalibracao observacional.",
	),
]


def open_connection(database_path: Path) -> sqlite3.Connection:
	connection = sqlite3.connect(database_path)
	connection.row_factory = sqlite3.Row
	connection.execute("PRAGMA foreign_keys = ON")
	connection.execute("PRAGMA journal_mode = WAL")
	connection.execute("PRAGMA synchronous = FULL")
	return connection


def bootstrap_db(config: AppConfig) -> BootstrapSummary:
	ensure_state_directories(config.paths)
	connection = open_connection(config.paths.database_path)
	try:
		apply_schema(connection, config.paths.schema_path)
		ensure_bootstrap_data(connection, config)
		validate_single_active_aoi(connection)
	finally:
		connection.close()
	return BootstrapSummary(
		database_path=config.paths.database_path,
		schema_version=config.schema_version,
		active_aoi=config.active_aoi_name,
		seeded_policy_count=len(POLICY_ROWS),
	)


def apply_schema(connection: sqlite3.Connection, schema_path: Path) -> None:
	if not schema_path.exists():
		raise FileNotFoundError(f"Schema file not found: {schema_path}")
	connection.executescript(schema_path.read_text(encoding="utf-8"))


def ensure_bootstrap_data(connection: sqlite3.Connection, config: AppConfig) -> None:
	now_utc = current_utc()
	connection.executemany(
		"""
		INSERT OR IGNORE INTO schema_meta (name, value, updated_at_utc)
		VALUES (?, ?, ?)
		""",
		[
			("schema_version", config.schema_version, now_utc),
			("created_at_utc", now_utc, now_utc),
		],
	)
	connection.executemany(
		"""
		INSERT OR IGNORE INTO dataset_policy (
			policy_id, dataset, policy_version, enabled, priority, prediction_mode,
			pre_window_hours, active_window_hours, retry_backoff_hours_json,
			stale_after_hours, max_retry_count, query_margin_hours,
			download_enabled, min_confidence, notes, created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		[row + (now_utc, now_utc) for row in POLICY_ROWS],
	)
	aoi_hash = _file_hash(config.paths.validation_aoi_path)
	connection.execute(
		"""
		INSERT INTO aois (
			aoi_id, aoi_name, aoi_type, geometry_path, geometry_hash, crs,
			is_active, created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(aoi_id) DO UPDATE SET
			aoi_name = excluded.aoi_name,
			geometry_path = excluded.geometry_path,
			geometry_hash = excluded.geometry_hash,
			crs = excluded.crs,
			is_active = excluded.is_active,
			updated_at_utc = excluded.updated_at_utc
		""",
		(
			config.active_aoi_id,
			config.active_aoi_name,
			"biome",
			str(config.paths.validation_aoi_path),
			aoi_hash,
			"EPSG:4326",
			1,
			now_utc,
			now_utc,
		),
	)
	connection.commit()


def validate_single_active_aoi(connection: sqlite3.Connection) -> None:
	count = connection.execute("SELECT COUNT(*) AS count FROM aois WHERE is_active = 1").fetchone()["count"]
	if count != 1:
		raise RuntimeError(f"Expected exactly one active AOI, found {count}.")


def current_utc() -> str:
	from datetime import UTC, datetime

	return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _file_hash(file_path: Path) -> str:
	if not file_path.exists():
		raise FileNotFoundError(f"AOI geometry file not found: {file_path}")
	return hashlib.sha256(file_path.read_bytes()).hexdigest()