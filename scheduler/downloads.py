from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any, Callable

import requests

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
		SELECT pa.asset_uid, pa.product_uid, pa.asset_key, pa.source_url, pa.filename, pa.local_path, p.dataset
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
			_finish_download(connection, download_id, bytes_written, expected_bytes, "succeeded", http_status=http_status)
			_mark_asset_downloaded(connection, asset["asset_uid"], destination, asset["product_uid"])
			results["downloaded_assets"] += 1
		except Exception as error:
			_mark_asset_failed(connection, asset["asset_uid"])
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


def _mark_asset_downloaded(connection: sqlite3.Connection, asset_uid: str, destination: Path, product_uid: str) -> None:
	now_utc = current_utc()
	connection.execute(
		"UPDATE product_assets SET local_path = ?, asset_status = 'downloaded', completed_at_utc = ?, integrity_status = 'ok', updated_at_utc = ? WHERE asset_uid = ?",
		(str(destination), now_utc, now_utc, asset_uid),
	)
	remaining = connection.execute(
		"SELECT COUNT(*) FROM product_assets WHERE product_uid = ? AND asset_status != 'downloaded'",
		(product_uid,),
	).fetchone()[0]
	if remaining == 0:
		connection.execute(
			"UPDATE products SET current_status = 'downloaded', updated_at_utc = ? WHERE product_uid = ?",
			(now_utc, product_uid),
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