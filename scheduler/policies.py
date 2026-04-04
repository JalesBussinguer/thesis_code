from __future__ import annotations

import sqlite3


def get_enabled_policy(connection: sqlite3.Connection, dataset: str) -> sqlite3.Row | None:
	return connection.execute(
		"SELECT * FROM dataset_policy WHERE dataset = ? AND enabled = 1 ORDER BY policy_version DESC LIMIT 1",
		(dataset.upper(),),
	).fetchone()


def list_enabled_policies(connection: sqlite3.Connection) -> list[sqlite3.Row]:
	return connection.execute(
		"SELECT * FROM dataset_policy WHERE enabled = 1 ORDER BY priority ASC, dataset ASC"
	).fetchall()