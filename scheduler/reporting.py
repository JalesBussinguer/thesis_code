from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def export_run_report(connection: sqlite3.Connection, config, run_id: str) -> dict[str, Path]:
	run_row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
	if run_row is None:
		raise ValueError(f"Run not found: {run_id}")
	step_rows = connection.execute(
		"SELECT step_name, status, records_in, records_out, error_class, error_message FROM run_steps WHERE run_id = ? ORDER BY started_at_utc ASC",
		(run_id,),
	).fetchall()
	summary = {
		"run_id": run_id,
		"mode": run_row["mode"],
		"status": run_row["status"],
		"started_at_utc": run_row["started_at_utc"],
		"ended_at_utc": run_row["ended_at_utc"],
		"notes": run_row["notes"],
		"counts": {
			"predicted_events": _count(connection, "SELECT COUNT(*) FROM predicted_events"),
			"query_windows": _count(connection, "SELECT COUNT(*) FROM query_windows WHERE executed_in_run_id = ?", run_id),
			"products_detected": _count(connection, "SELECT COUNT(*) FROM products WHERE first_detected_at_utc >= ? AND first_detected_at_utc <= COALESCE(?, first_detected_at_utc)", run_row["started_at_utc"], run_row["ended_at_utc"]),
			"downloads": _count(connection, "SELECT COUNT(*) FROM downloads WHERE run_id = ?", run_id),
			"successful_downloads": _count(connection, "SELECT COUNT(*) FROM downloads WHERE run_id = ? AND status = 'succeeded'", run_id),
			"quarantined_assets": _count(connection, "SELECT COUNT(*) FROM downloads d JOIN product_assets pa ON pa.asset_uid = d.asset_uid WHERE d.run_id = ? AND pa.asset_status = 'quarantined'", run_id),
			"quarantined_products": _count(connection, "SELECT COUNT(DISTINCT pa.product_uid) FROM downloads d JOIN product_assets pa ON pa.asset_uid = d.asset_uid JOIN products p ON p.product_uid = pa.product_uid WHERE d.run_id = ? AND p.current_status = 'quarantined'", run_id),
			"api_observations": _count(connection, "SELECT COUNT(*) FROM api_observations WHERE run_id = ?", run_id),
			"integrity_checks": _count(connection, "SELECT COUNT(*) FROM file_integrity_checks WHERE run_id = ?", run_id),
			"anomalies": _count(connection, "SELECT COUNT(*) FROM anomalies WHERE run_id = ?", run_id),
		},
		"catalog_dataset_summary": _dataset_summary(connection),
		"predicted_event_status_summary": _grouped_counts(connection, "SELECT status, COUNT(*) AS total FROM predicted_events GROUP BY status", "status"),
		"product_status_summary": _grouped_counts(connection, "SELECT current_status, COUNT(*) AS total FROM products GROUP BY current_status", "current_status"),
		"asset_status_summary": _grouped_counts(connection, "SELECT asset_status, COUNT(*) AS total FROM product_assets GROUP BY asset_status", "asset_status"),
		"steps": [dict(row) for row in step_rows],
		"anomalies": [
			dict(row)
			for row in connection.execute(
				"SELECT severity, anomaly_type, entity_type, entity_id, status, details_json FROM anomalies WHERE run_id = ? ORDER BY detected_at_utc ASC",
				(run_id,),
			).fetchall()
		],
	}
	json_path = config.paths.exports_dir / f"{run_id}_summary.json"
	markdown_path = config.paths.reports_dir / f"{run_id}_summary.md"
	json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
	markdown_path.write_text(_render_markdown(summary), encoding="utf-8")
	return {"json": json_path, "markdown": markdown_path}


def _count(connection: sqlite3.Connection, query: str, *params) -> int:
	return int(connection.execute(query, params).fetchone()[0])


def _grouped_counts(connection: sqlite3.Connection, query: str, key_column: str) -> list[dict[str, int | str]]:
	return [
		{"key": row[key_column], "total": int(row["total"])}
		for row in connection.execute(query).fetchall()
	]


def _dataset_summary(connection: sqlite3.Connection) -> list[dict[str, int | str]]:
	rows = connection.execute(
		"""
		SELECT
			p.dataset,
			COUNT(DISTINCT p.product_uid) AS products,
			SUM(CASE WHEN p.current_status = 'quarantined' THEN 1 ELSE 0 END) AS quarantined_products,
			COUNT(pa.asset_uid) AS assets,
			SUM(CASE WHEN pa.asset_status = 'downloaded' THEN 1 ELSE 0 END) AS downloaded_assets,
			SUM(CASE WHEN pa.asset_status = 'quarantined' THEN 1 ELSE 0 END) AS quarantined_assets,
			SUM(CASE WHEN pa.integrity_status = 'failed' THEN 1 ELSE 0 END) AS failed_integrity_assets,
			SUM(CASE WHEN pa.integrity_status = 'suspicious' THEN 1 ELSE 0 END) AS suspicious_integrity_assets
		FROM products p
		LEFT JOIN product_assets pa ON pa.product_uid = p.product_uid
		GROUP BY p.dataset
		ORDER BY p.dataset ASC
		"""
	).fetchall()
	return [
		{
			"dataset": row["dataset"],
			"products": int(row["products"] or 0),
			"quarantined_products": int(row["quarantined_products"] or 0),
			"assets": int(row["assets"] or 0),
			"downloaded_assets": int(row["downloaded_assets"] or 0),
			"quarantined_assets": int(row["quarantined_assets"] or 0),
			"failed_integrity_assets": int(row["failed_integrity_assets"] or 0),
			"suspicious_integrity_assets": int(row["suspicious_integrity_assets"] or 0),
		}
		for row in rows
	]


def _render_markdown(summary: dict) -> str:
	counts = summary["counts"]
	lines = [
		f"# Run Summary: {summary['run_id']}",
		"",
		f"- Mode: {summary['mode']}",
		f"- Status: {summary['status']}",
		f"- Started: {summary['started_at_utc']}",
		f"- Ended: {summary['ended_at_utc']}",
		f"- Notes: {summary['notes'] or ''}",
		"",
		"## Counts",
		"",
		f"- Query windows: {counts['query_windows']}",
		f"- Predicted events: {counts['predicted_events']}",
		f"- Products detected: {counts['products_detected']}",
		f"- Downloads: {counts['downloads']}",
		f"- Successful downloads: {counts['successful_downloads']}",
		f"- Quarantined assets: {counts['quarantined_assets']}",
		f"- Quarantined products: {counts['quarantined_products']}",
		f"- API observations: {counts['api_observations']}",
		f"- Integrity checks: {counts['integrity_checks']}",
		f"- Anomalies: {counts['anomalies']}",
		"",
		"## Catalog Dataset Summary",
		"",
	]
	for row in summary["catalog_dataset_summary"]:
		lines.append(
			f"- {row['dataset']}: products={row['products']} | assets={row['assets']} | downloaded_assets={row['downloaded_assets']} | quarantined_assets={row['quarantined_assets']} | failed_integrity_assets={row['failed_integrity_assets']}"
		)
	lines.extend(["", "## Predicted Event Status", ""])
	for row in summary["predicted_event_status_summary"]:
		lines.append(f"- {row['key']}: {row['total']}")
	lines.extend(["", "## Product Status", ""])
	for row in summary["product_status_summary"]:
		lines.append(f"- {row['key']}: {row['total']}")
	lines.extend(["", "## Asset Status", ""])
	for row in summary["asset_status_summary"]:
		lines.append(f"- {row['key']}: {row['total']}")
	lines.extend(["", "## Steps", ""])
	for step in summary["steps"]:
		lines.append(
			f"- {step['step_name']}: {step['status']}"
			+ (f" | in={step['records_in']}" if step.get("records_in") is not None else "")
			+ (f" | out={step['records_out']}" if step.get("records_out") is not None else "")
			+ (f" | error={step['error_class']}: {step['error_message']}" if step.get("error_class") else "")
		)
	if summary["anomalies"]:
		lines.extend(["", "## Anomalies", ""])
		for anomaly in summary["anomalies"]:
			lines.append(
				f"- {anomaly['severity']} | {anomaly['anomaly_type']} | {anomaly['entity_type']} {anomaly['entity_id']} | {anomaly['status']}"
			)
	return "\n".join(lines) + "\n"