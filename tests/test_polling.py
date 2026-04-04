from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scheduler.cli import main as cli_main
from scheduler.config import load_config
from scheduler.db import open_connection
from scheduler.main_runner import run
from tests.fixtures.db_helpers import bootstrap_temp_db
from tests.fixtures.geometry_data import make_square_aoi_feature, write_geojson


class _FakeResponse:
	def __init__(self, payload):
		self._payload = payload

	def raise_for_status(self) -> None:
		return None

	def json(self):
		return self._payload


def _fake_asf_get(url, params=None, timeout=None):
	return _FakeResponse(
		[
			{
				"product_file_id": "S1_TEST_001",
				"sceneName": "S1_TEST_SCENE_001",
				"platform": "Sentinel-1A",
				"processingLevel": "SLC",
				"relativeOrbit": 24,
				"absoluteOrbit": 62024,
				"pathNumber": 24,
				"frameNumber": 603,
				"beamModeType": "IW",
				"flightDirection": "DESCENDING",
				"startTime": "2026-03-01T08:41:37Z",
				"stopTime": "2026-03-01T08:46:52Z",
				"stringFootprint": "POLYGON((-0.5 -0.5, -0.5 0.5, 0.5 0.5, 0.5 -0.5, -0.5 -0.5))",
				"downloadUrl": "https://example.com/s1.zip",
				"fileName": "s1.zip",
			}
		]
	)


class _FakeBiomassItem:
	def __init__(self):
		self.id = "BIO_TEST_001"
		self.collection_id = "BiomassLevel1a"
		self.properties = {
			"title": "BIO_S1_SCS__1S_20260301T094741_20260301T094802_T_G01_M01_C02_T006_F289_01_TEST",
			"sat:orbit_state": "ascending",
			"sat:absolute_orbit": 5022,
			"start_datetime": "2026-03-01T09:47:41Z",
			"end_datetime": "2026-03-01T09:48:02Z",
		}
		self.geometry = {
			"type": "Polygon",
			"coordinates": [[[-0.5, -0.5], [-0.5, 0.5], [0.5, 0.5], [0.5, -0.5], [-0.5, -0.5]]],
		}
		self.assets = {"product": {"href": "https://example.com/biomass.zip"}}


def _fake_biomass_search(**kwargs):
	return [_FakeBiomassItem()]


def _fake_downloader(url: str, destination: Path):
	destination.parent.mkdir(parents=True, exist_ok=True)
	destination.write_bytes(b"test-bytes")
	return len(b"test-bytes"), len(b"test-bytes")


class PollingTests(unittest.TestCase):
	def setUp(self) -> None:
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		self.workspace_dir = Path(self.temp_dir.name)
		self.state_dir = self.workspace_dir / "scheduler_state"
		self.search_aoi_path = self.workspace_dir / "datasets" / "cerrado_bbox.geojson"
		self.validation_aoi_path = self.workspace_dir / "datasets" / "cerrado_border.geojson"
		write_geojson(self.search_aoi_path, [make_square_aoi_feature(min_x=-2.0, min_y=-2.0, max_x=2.0, max_y=2.0)])
		write_geojson(self.validation_aoi_path, [make_square_aoi_feature()])
		os.environ["SCHEDULER_WORKSPACE_ROOT"] = str(self.workspace_dir)
		os.environ["SCHEDULER_SEARCH_AOI_PATH"] = str(self.search_aoi_path)
		os.environ["SCHEDULER_VALIDATION_AOI_PATH"] = str(self.validation_aoi_path)
		self.addCleanup(os.environ.pop, "SCHEDULER_WORKSPACE_ROOT", None)
		self.addCleanup(os.environ.pop, "SCHEDULER_SEARCH_AOI_PATH", None)
		self.addCleanup(os.environ.pop, "SCHEDULER_VALIDATION_AOI_PATH", None)
		self.db_path = bootstrap_temp_db(self.workspace_dir, self.state_dir)
		with closing(open_connection(self.db_path)) as connection:
			connection.execute(
				"""
				INSERT INTO orbit_baseline (
					orbit_scope_key, dataset, platform, flight_direction, path_number,
					frame_number, beam_mode, mode, orbit_state, track_number, frame_code,
					first_seen_acquisition_utc, last_seen_acquisition_utc, median_gap_hours,
					p90_gap_hours, historical_scene_count, confidence_score, last_calibrated_at_utc,
					baseline_source, created_at_utc, updated_at_utc
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				""",
				(
					"SENTINEL-1|Sentinel-1A|DESCENDING|24|IW",
					"SENTINEL-1",
					"Sentinel-1A",
					"DESCENDING",
					24,
					603,
					"IW",
					None,
					None,
					None,
					None,
					"2026-03-01T08:41:37Z",
					"2026-03-01T08:46:52Z",
					24.0,
					24.0,
					1,
					0.9,
					"2026-04-04T00:00:00Z",
					"live_inventory",
					"2026-04-04T00:00:00Z",
					"2026-04-04T00:00:00Z",
				),
			)
			connection.execute(
				"""
				INSERT INTO orbit_download_allowlist (
					allowlist_id, aoi_id, orbit_scope_key, allow_status, allow_reason,
					auto_discovered, first_added_at_utc, last_reviewed_at_utc,
					source_coverage_id, source_run_id, notes, created_at_utc, updated_at_utc
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				""",
				(
					"allow-1",
					"cerrado_biome_v1",
					"SENTINEL-1|Sentinel-1A|DESCENDING|24|IW",
					"allowed",
					"manual_override",
					0,
					"2026-04-04T00:00:00Z",
					"2026-04-04T00:00:00Z",
					None,
					None,
					None,
					"2026-04-04T00:00:00Z",
					"2026-04-04T00:00:00Z",
				),
			)
			connection.commit()

	def _load_config(self):
		from argparse import Namespace

		return load_config(Namespace(state_dir=str(self.state_dir), db_path=None, verbose=False))

	def test_poll_only_creates_query_window_and_products(self) -> None:
		summary = run(self._load_config(), "poll-only", request_get={"SENTINEL-1": _fake_asf_get})
		self.assertTrue(summary.run_id.startswith("run-"))
		with closing(sqlite3.connect(self.db_path)) as connection:
			window_count = connection.execute("SELECT COUNT(*) FROM query_windows").fetchone()[0]
			product_count = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
			obs_count = connection.execute("SELECT COUNT(*) FROM api_observations").fetchone()[0]
		self.assertEqual(window_count, 1)
		self.assertEqual(product_count, 1)
		self.assertEqual(obs_count, 1)
		with closing(sqlite3.connect(self.db_path)) as connection:
			queue_count = connection.execute("SELECT COUNT(*) FROM poll_queue WHERE queue_state = 'completed'").fetchone()[0]
		self.assertEqual(queue_count, 1)

	def test_poll_only_supports_biomass_provider_dispatch(self) -> None:
		with closing(open_connection(self.db_path)) as connection:
			connection.execute(
				"""
				INSERT INTO orbit_baseline (
					orbit_scope_key, dataset, platform, flight_direction, path_number,
					frame_number, beam_mode, mode, orbit_state, track_number, frame_code,
					first_seen_acquisition_utc, last_seen_acquisition_utc, median_gap_hours,
					p90_gap_hours, historical_scene_count, confidence_score, last_calibrated_at_utc,
					baseline_source, created_at_utc, updated_at_utc
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				""",
				(
					"BIOMASS|SCS|ASCENDING|6|289",
					"BIOMASS",
					"BIOMASS",
					"ASCENDING",
					None,
					289,
					None,
					"SCS",
					"ASCENDING",
					6,
					"F289",
					"2026-03-01T09:47:41Z",
					"2026-03-01T09:48:02Z",
					72.0,
					72.0,
					1,
					0.8,
					"2026-04-04T00:00:00Z",
					"live_inventory",
					"2026-04-04T00:00:00Z",
					"2026-04-04T00:00:00Z",
				),
			)
			connection.execute(
				"""
				INSERT INTO orbit_download_allowlist (
					allowlist_id, aoi_id, orbit_scope_key, allow_status, allow_reason,
					auto_discovered, first_added_at_utc, last_reviewed_at_utc,
					source_coverage_id, source_run_id, notes, created_at_utc, updated_at_utc
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				""",
				(
					"allow-2",
					"cerrado_biome_v1",
					"BIOMASS|SCS|ASCENDING|6|289",
					"allowed",
					"manual_override",
					0,
					"2026-04-04T00:00:00Z",
					"2026-04-04T00:00:00Z",
					None,
					None,
					None,
					"2026-04-04T00:00:00Z",
					"2026-04-04T00:00:00Z",
				),
			)
			connection.commit()
		run(self._load_config(), "poll-only", request_get={"SENTINEL-1": _fake_asf_get, "BIOMASS": _fake_biomass_search})
		with closing(sqlite3.connect(self.db_path)) as connection:
			count = connection.execute("SELECT COUNT(*) FROM products WHERE dataset = 'BIOMASS'").fetchone()[0]
		self.assertGreaterEqual(count, 1)

	def test_download_only_downloads_queued_assets(self) -> None:
		run(self._load_config(), "poll-only", request_get={"SENTINEL-1": _fake_asf_get})
		summary = run(self._load_config(), "download-only", downloader=_fake_downloader)
		self.assertTrue(summary.run_id.startswith("run-"))
		with closing(sqlite3.connect(self.db_path)) as connection:
			asset_status = connection.execute("SELECT asset_status FROM product_assets WHERE asset_key = 'primary'").fetchone()[0]
			download_count = connection.execute("SELECT COUNT(*) FROM downloads WHERE status = 'succeeded'").fetchone()[0]
		self.assertEqual(asset_status, "downloaded")
		self.assertEqual(download_count, 1)

	def test_full_run_polls_and_downloads_in_single_run(self) -> None:
		summary = run(
			self._load_config(),
			"full-run",
			request_get={"SENTINEL-1": _fake_asf_get},
			downloader={"SENTINEL-1": _fake_downloader},
		)
		self.assertTrue(summary.run_id.startswith("run-"))
		with closing(sqlite3.connect(self.db_path)) as connection:
			connection.row_factory = sqlite3.Row
			product_status = connection.execute("SELECT current_status FROM products").fetchone()[0]
			steps = {row[0] for row in connection.execute("SELECT step_name FROM run_steps WHERE run_id = ?", (summary.run_id,)).fetchall()}
		self.assertEqual(product_status, "downloaded")
		self.assertIn("execute_polling", steps)
		self.assertIn("execute_downloads", steps)

	def test_cli_poll_only_succeeds_with_seeded_orbit(self) -> None:
		from unittest.mock import patch

		with patch("scheduler.providers.asf.requests.get", side_effect=_fake_asf_get):
			exit_code = cli_main(["--mode", "poll-only", "--state-dir", str(self.state_dir)])
		self.assertEqual(exit_code, 0)


if __name__ == "__main__":
	unittest.main()