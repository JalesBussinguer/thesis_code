from __future__ import annotations

import sqlite3
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable

import requests

from .anomalies import create_anomaly
from .auth import build_asf_session, get_biomass_access_token
from .db import current_utc


def queue_product_assets(connection: sqlite3.Connection, product_uid: str) -> int:
	connection.execute(
		"UPDATE product_assets SET asset_status = 'queued', updated_at_utc = ? WHERE product_uid = ? AND asset_status = 'discovered'",
		(current_utc(), product_uid),
	)
	connection.execute(
		"UPDATE products SET current_status = 'queued_for_download', updated_at_utc = ? WHERE product_uid = ? AND current_status IN ('eligible', 'discovered')",
		(current_utc(), product_uid),
	)
	connection.commit()
	row = connection.execute("SELECT COUNT(*) FROM product_assets WHERE product_uid = ? AND asset_status = 'queued'", (product_uid,)).fetchone()
	return row[0]


def list_queued_assets(connection: sqlite3.Connection) -> list[sqlite3.Row]:
	return connection.execute(
		"""
		SELECT pa.asset_uid, pa.product_uid, pa.asset_key, pa.source_url, pa.filename, pa.local_path, p.dataset, p.first_query_window_id, p.aoi_id
		FROM product_assets pa
		JOIN products p ON p.product_uid = pa.product_uid
		WHERE pa.asset_status = 'queued'
		ORDER BY p.dataset ASC, pa.product_uid ASC, pa.asset_key ASC
		"""
	).fetchall()


def execute_download_only(
	connection: sqlite3.Connection,
	config,
	run_id: str,
	downloader: Callable[[str, Path], tuple[int, int | None]] | None = None,
) -> dict[str, int]:
	assets = list_queued_assets(connection)
	runtime = _DownloadRuntime(config.paths.credentials_path)
	results = {"queued_assets": len(assets), "downloaded_assets": 0}
	for asset in assets:
		destination = _build_destination_path(config.paths.state_dir, asset["dataset"], asset["filename"])
		download_id = _start_download(connection, run_id, asset["asset_uid"], destination)
		try:
			selected_downloader = _resolve_downloader(downloader, asset["dataset"])
			if selected_downloader is not None:
				bytes_written, expected_bytes = selected_downloader(asset["source_url"], destination)
				http_status = 200
			else:
				bytes_written, expected_bytes, http_status = _download_with_provider_auth(asset, destination, runtime)
			integrity_summary = _run_integrity_checks(connection, run_id, asset["asset_uid"], destination, expected_bytes)
			_finish_download(connection, download_id, bytes_written, expected_bytes, "succeeded", http_status=http_status)
			_insert_download_observation(
				connection,
				query_window_id=asset["first_query_window_id"],
				run_id=run_id,
				dataset=asset["dataset"],
				asset_uid=asset["asset_uid"],
				http_status=http_status,
				observation_status="ok" if integrity_summary["status"] == "ok" else "partial",
				notes=integrity_summary["notes"],
			)
			_record_integrity_anomaly(connection, run_id, asset, download_id, integrity_summary)
			asset_status = _mark_asset_downloaded(
				connection,
				asset["asset_uid"],
				destination,
				asset["product_uid"],
				integrity_summary["status"],
				integrity_summary["notes"],
			)
			if asset_status == "downloaded":
				results["downloaded_assets"] += 1
		except Exception as error:
			_mark_asset_failed(connection, asset["asset_uid"])
			create_anomaly(
				connection,
				run_id=run_id,
				aoi_id=asset["aoi_id"],
				dataset=asset["dataset"],
				entity_type="download",
				entity_id=download_id,
				severity="error",
				anomaly_type="download_failed",
				details={
					"asset_uid": asset["asset_uid"],
					"error_class": error.__class__.__name__,
					"error_message": str(error),
				},
			)
			_insert_download_observation(
				connection,
				query_window_id=asset["first_query_window_id"],
				run_id=run_id,
				dataset=asset["dataset"],
				asset_uid=asset["asset_uid"],
				http_status=_extract_http_status(error),
				observation_status="failed",
				notes=str(error),
			)
			_finish_download(
				connection,
				download_id,
				None,
				None,
				"failed",
				error.__class__.__name__,
				str(error),
				http_status=_extract_http_status(error),
			)
			raise
	return results


def _record_integrity_anomaly(connection: sqlite3.Connection, run_id: str, asset: sqlite3.Row, download_id: str, integrity_summary: dict[str, str | None]) -> None:
	if integrity_summary["status"] == "ok":
		return
	severity = "warning" if integrity_summary["status"] == "suspicious" else "error"
	anomaly_type = "integrity_warning" if integrity_summary["status"] == "suspicious" else "integrity_failed"
	create_anomaly(
		connection,
		run_id=run_id,
		aoi_id=asset["aoi_id"],
		dataset=asset["dataset"],
		entity_type="asset",
		entity_id=asset["asset_uid"],
		severity=severity,
		anomaly_type=anomaly_type,
		details={
			"download_id": download_id,
			"product_uid": asset["product_uid"],
			"notes": integrity_summary["notes"],
		},
	)


class _DownloadRuntime:
	def __init__(self, credentials_path: Path):
		self.credentials_path = credentials_path
		self._asf_session = None
		self._biomass_access_token = None

	def get_asf_session(self):
		if self._asf_session is None:
			self._asf_session = build_asf_session(self.credentials_path)
		return self._asf_session

	def get_biomass_access_token(self) -> str:
		if self._biomass_access_token is None:
			self._biomass_access_token = get_biomass_access_token(self.credentials_path)
		return self._biomass_access_token


def _build_destination_path(state_dir: Path, dataset: str, filename: str) -> Path:
	destination = state_dir / "downloads" / dataset.lower().replace(" ", "_") / filename
	destination.parent.mkdir(parents=True, exist_ok=True)
	return destination


def _start_download(connection: sqlite3.Connection, run_id: str, asset_uid: str, destination: Path) -> str:
	now_utc = current_utc()
	download_id = f"download-{uuid.uuid4()}"
	connection.execute(
		"""
		INSERT INTO downloads (
			download_id, run_id, asset_uid, destination_path, temp_path, started_at_utc,
			ended_at_utc, bytes_expected, bytes_written, status, http_status,
			error_class, error_message, created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(
			download_id,
			run_id,
			asset_uid,
			str(destination),
			str(destination.with_suffix(destination.suffix + ".part")),
			now_utc,
			None,
			None,
			None,
			"running",
			None,
			None,
			None,
			now_utc,
			now_utc,
		),
	)
	connection.execute(
		"UPDATE product_assets SET asset_status = 'downloading', first_download_attempt_at_utc = COALESCE(first_download_attempt_at_utc, ?), last_download_attempt_at_utc = ?, updated_at_utc = ? WHERE asset_uid = ?",
		(now_utc, now_utc, now_utc, asset_uid),
	)
	connection.commit()
	return download_id


def _finish_download(
	connection: sqlite3.Connection,
	download_id: str,
	bytes_written: int | None,
	bytes_expected: int | None,
	status: str,
	error_class: str | None = None,
	error_message: str | None = None,
	http_status: int | None = None,
) -> None:
	now_utc = current_utc()
	connection.execute(
		"""
		UPDATE downloads
		SET ended_at_utc = ?, bytes_expected = ?, bytes_written = ?, status = ?, http_status = ?, error_class = ?, error_message = ?, updated_at_utc = ?
		WHERE download_id = ?
		""",
		(now_utc, bytes_expected, bytes_written, status, http_status, error_class, error_message, now_utc, download_id),
	)
	connection.commit()


def _mark_asset_downloaded(
	connection: sqlite3.Connection,
	asset_uid: str,
	destination: Path,
	product_uid: str,
	integrity_status: str,
	integrity_notes: str | None,
) -> str:
	now_utc = current_utc()
	asset_status = "quarantined" if integrity_status == "failed" else "downloaded"
	connection.execute(
		"UPDATE product_assets SET local_path = ?, asset_status = ?, completed_at_utc = ?, integrity_status = ?, integrity_notes = ?, updated_at_utc = ? WHERE asset_uid = ?",
		(str(destination), asset_status, now_utc, integrity_status, integrity_notes, now_utc, asset_uid),
	)
	product_status = _resolve_product_status(connection, product_uid)
	if product_status is not None:
		connection.execute(
			"UPDATE products SET current_status = ?, updated_at_utc = ? WHERE product_uid = ?",
			(product_status, now_utc, product_uid),
		)
	connection.commit()
	return asset_status


def _resolve_product_status(connection: sqlite3.Connection, product_uid: str) -> str | None:
	rows = connection.execute(
		"SELECT asset_status FROM product_assets WHERE product_uid = ?",
		(product_uid,),
	).fetchall()
	statuses = {row[0] for row in rows}
	if not statuses:
		return None
	if "quarantined" in statuses:
		return "quarantined"
	if statuses <= {"downloaded"}:
		return "downloaded"
	return None


def _run_integrity_checks(
	connection: sqlite3.Connection,
	run_id: str,
	asset_uid: str,
	destination: Path,
	expected_bytes: int | None,
) -> dict[str, str | None]:
	results = [
		_record_integrity_check(
			connection,
			run_id,
			asset_uid,
			"exists",
			"passed" if destination.exists() else "failed",
			observed_value=str(destination),
			notes=None if destination.exists() else "Downloaded file is missing from disk.",
		),
	]
	file_size = destination.stat().st_size if destination.exists() else 0
	results.append(
		_record_integrity_check(
			connection,
			run_id,
			asset_uid,
			"size_nonzero",
			"passed" if file_size > 0 else "failed",
			observed_value=str(file_size),
			notes=None if file_size > 0 else "Downloaded file is empty.",
		)
	)
	if expected_bytes is not None:
		results.append(
			_record_integrity_check(
				connection,
				run_id,
				asset_uid,
				"size_matches_header",
				"passed" if file_size == expected_bytes else "warning",
				expected_value=str(expected_bytes),
				observed_value=str(file_size),
				notes=None if file_size == expected_bytes else "Downloaded size differs from response header.",
			)
		)
	extension = destination.suffix.lower()
	results.append(
		_record_integrity_check(
			connection,
			run_id,
			asset_uid,
			"extension_expected",
			"passed" if extension else "warning",
			observed_value=extension or "<none>",
			notes=None if extension else "Downloaded file has no extension.",
		)
	)
	if extension == ".zip":
		try:
			with zipfile.ZipFile(destination, "r") as archive:
				archive.testzip()
			zip_result = "passed"
			zip_notes = None
		except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
			zip_result = "failed"
			zip_notes = str(error)
		results.append(
			_record_integrity_check(
				connection,
				run_id,
				asset_uid,
				"zip_open_test",
				zip_result,
				observed_value=destination.name,
				notes=zip_notes,
			)
		)
	statuses = {result["check_result"] for result in results}
	if "failed" in statuses:
		return {"status": "failed", "notes": _combine_integrity_notes(results, "failed")}
	if "warning" in statuses:
		return {"status": "suspicious", "notes": _combine_integrity_notes(results, "warning")}
	return {"status": "ok", "notes": None}


def _record_integrity_check(
	connection: sqlite3.Connection,
	run_id: str,
	asset_uid: str,
	check_type: str,
	check_result: str,
	expected_value: str | None = None,
	observed_value: str | None = None,
	notes: str | None = None,
) -> dict[str, str | None]:
	now_utc = current_utc()
	connection.execute(
		"""
		INSERT INTO file_integrity_checks (
			integrity_check_id, asset_uid, run_id, checked_at_utc, check_type,
			check_result, expected_value, observed_value, notes, created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(
			f"integrity-{uuid.uuid4()}",
			asset_uid,
			run_id,
			now_utc,
			check_type,
			check_result,
			expected_value,
			observed_value,
			notes,
			now_utc,
			now_utc,
		),
	)
	connection.commit()
	return {"check_type": check_type, "check_result": check_result, "notes": notes}


def _combine_integrity_notes(results: list[dict[str, str | None]], severity: str) -> str:
	notes = [result["notes"] for result in results if result["check_result"] == severity and result["notes"]]
	return " | ".join(notes)


def _insert_download_observation(
	connection: sqlite3.Connection,
	query_window_id: str | None,
	run_id: str,
	dataset: str,
	asset_uid: str,
	http_status: int | None,
	observation_status: str,
	notes: str | None,
) -> None:
	if not query_window_id:
		return
	now_utc = current_utc()
	endpoint_name = "esa_maap_download" if dataset.upper() == "BIOMASS" else "earthdata_download"
	request_fingerprint = f"{dataset.upper()}|{asset_uid}"
	connection.execute(
		"""
		INSERT INTO api_observations (
			observation_id, query_window_id, run_id, dataset, endpoint_name,
			request_fingerprint, observed_at_utc, http_status, parsed_record_count,
			observation_status, anomaly_flag, raw_payload_path, notes, created_at_utc, updated_at_utc
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(
			f"obs-{uuid.uuid4()}",
			query_window_id,
			run_id,
			dataset.upper(),
			endpoint_name,
			request_fingerprint,
			now_utc,
			http_status,
			1,
			observation_status,
			1 if observation_status in {"failed", "partial"} else 0,
			None,
			notes,
			now_utc,
			now_utc,
		),
	)
	connection.commit()


def _mark_asset_failed(connection: sqlite3.Connection, asset_uid: str) -> None:
	now_utc = current_utc()
	connection.execute(
		"UPDATE product_assets SET asset_status = 'failed', updated_at_utc = ? WHERE asset_uid = ?",
		(now_utc, asset_uid),
	)
	connection.commit()


def _resolve_downloader(downloader, dataset: str):
	if isinstance(downloader, dict):
		return downloader.get(dataset.upper())
	return downloader


def _download_with_provider_auth(asset: sqlite3.Row, destination: Path, runtime: _DownloadRuntime) -> tuple[int, int | None, int | None]:
	dataset = asset["dataset"].upper()
	if dataset in {"SENTINEL-1", "NISAR"}:
		return _download_with_session(runtime.get_asf_session(), asset["source_url"], destination)
	if dataset == "BIOMASS":
		return _download_with_bearer_token(asset["source_url"], destination, runtime.get_biomass_access_token())
		
	return _default_downloader(asset["source_url"], destination)


def _download_with_session(session: requests.Session, url: str, destination: Path) -> tuple[int, int | None, int | None]:
	temp_path = destination.with_suffix(destination.suffix + ".part")
	with session.get(url, stream=True, timeout=120) as response:
		return _stream_response_to_path(response, temp_path, destination)


def _download_with_bearer_token(url: str, destination: Path, access_token: str) -> tuple[int, int | None, int | None]:
	temp_path = destination.with_suffix(destination.suffix + ".part")
	with requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, stream=True, timeout=120) as response:
		return _stream_response_to_path(response, temp_path, destination)


def _stream_response_to_path(response, temp_path: Path, destination: Path) -> tuple[int, int | None, int | None]:
	response.raise_for_status()
	expected_bytes = int(response.headers.get("content-length", 0)) or None
	bytes_written = 0
	with temp_path.open("wb") as file_handle:
		for chunk in response.iter_content(chunk_size=1024 * 1024):
			if not chunk:
				continue
			bytes_written += file_handle.write(chunk)
	temp_path.replace(destination)
	return bytes_written, expected_bytes, getattr(response, "status_code", None)


def _extract_http_status(error: Exception) -> int | None:
	response = getattr(error, "response", None)
	if response is None:
		return None
	status_code = getattr(response, "status_code", None)
	return int(status_code) if status_code is not None else None


def _default_downloader(url: str, destination: Path) -> tuple[int, int | None, int | None]:
	temp_path = destination.with_suffix(destination.suffix + ".part")
	with requests.get(url, stream=True, timeout=120) as response:
		return _stream_response_to_path(response, temp_path, destination)