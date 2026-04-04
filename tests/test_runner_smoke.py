from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scheduler.cli import main as cli_main
from scheduler.lease import seed_active_lease
from scheduler.main_runner import run
from scheduler.config import load_config

from tests.fixtures.db_helpers import bootstrap_temp_db
from tests.fixtures.geometry_data import make_square_aoi_feature, write_geojson


class RunnerSmokeTests(unittest.TestCase):
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

	def _load_config(self):
		from argparse import Namespace

		return load_config(Namespace(state_dir=str(self.state_dir), db_path=None, verbose=False))

	def test_runner_dry_run_creates_run(self) -> None:
		exit_code = cli_main(["--mode", "dry-run", "--state-dir", str(self.state_dir)])
		self.assertEqual(exit_code, 0)
		with closing(sqlite3.connect(self.db_path)) as connection:
			count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
		self.assertEqual(count, 1)

	def test_runner_dry_run_writes_steps(self) -> None:
		cli_main(["--mode", "dry-run", "--state-dir", str(self.state_dir)])
		with closing(sqlite3.connect(self.db_path)) as connection:
			steps = {
				row[0]
				for row in connection.execute("SELECT step_name FROM run_steps").fetchall()
			}
		self.assertIn("acquire_lease", steps)
		self.assertIn("load_state", steps)
		self.assertIn("export_reports", steps)
		self.assertIn("release_lease", steps)

	def test_runner_exports_report_files(self) -> None:
		summary = run(self._load_config(), "dry-run")
		markdown_report = self.state_dir / "reports" / f"{summary.run_id}_summary.md"
		json_report = self.state_dir / "exports" / f"{summary.run_id}_summary.json"
		self.assertTrue(markdown_report.exists())
		self.assertTrue(json_report.exists())
		report_payload = json.loads(json_report.read_text(encoding="utf-8"))
		self.assertIn("catalog_dataset_summary", report_payload)
		self.assertIn("asset_status_summary", report_payload)
		self.assertIn("predicted_event_status_summary", report_payload)

	def test_runner_releases_lease_on_success(self) -> None:
		cli_main(["--mode", "dry-run", "--state-dir", str(self.state_dir)])
		with closing(sqlite3.connect(self.db_path)) as connection:
			count = connection.execute("SELECT COUNT(*) FROM run_lease").fetchone()[0]
		self.assertEqual(count, 0)

	def test_runner_blocks_basic_concurrency(self) -> None:
		config = self._load_config()
		with closing(sqlite3.connect(self.db_path)) as connection:
			connection.row_factory = sqlite3.Row
			connection.execute(
				"INSERT INTO runs (run_id, trigger_type, mode, host, pid, started_at_utc, ended_at_utc, status, exit_code, scheduler_version, dry_run, config_hash, notes, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
				(
					"run-active",
					"manual",
					"dry_run",
					"localhost",
					None,
					"2026-04-04T00:00:00Z",
					None,
					"running",
					None,
					"0.1.0",
					1,
					"phase0",
					None,
					"2026-04-04T00:00:00Z",
					"2026-04-04T00:00:00Z",
				),
			)
			connection.commit()
			seed_active_lease(connection, "run-active")
		self.assertEqual(cli_main(["--mode", "dry-run", "--state-dir", str(self.state_dir)]), 4)

	def test_runner_dry_run_performs_no_network_work(self) -> None:
		summary = run(self._load_config(), "dry-run")
		self.assertTrue(summary.run_id.startswith("run-"))
		with closing(sqlite3.connect(self.db_path)) as connection:
			count = connection.execute("SELECT COUNT(*) FROM api_observations").fetchone()[0]
		self.assertEqual(count, 0)


if __name__ == "__main__":
	unittest.main()