from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .db import current_utc


def create_anomaly(
	connection: sqlite3.Connection,
	run_id: str | None,
	aoi_id: str | None,
	dataset: str,
	entity_type: str,
	entity_id: str,
	severity: str,
	anomaly_type: str,
	details: dict[str, Any] | None = None,
	status: str = "open",
) -> str:
	now_utc = current_utc()
	anomaly_id = f"anomaly-{uuid.uuid4()}"
	connection.execute(
		"""
		INSERT INTO anomalies (
			anomaly_id, run_id, aoi_id, dataset, entity_type, entity_id,
			severity, anomaly_type, detected_at_utc, status, details_json,
			created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(
			anomaly_id,
			run_id,
			aoi_id,
			dataset.upper(),
			entity_type,
			entity_id,
			severity,
			anomaly_type,
			now_utc,
			status,
			json.dumps(details, ensure_ascii=True) if details is not None else None,
			now_utc,
			now_utc,
		),
	)
	connection.commit()
	return anomaly_id