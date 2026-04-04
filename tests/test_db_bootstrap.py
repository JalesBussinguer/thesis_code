from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scheduler.cli import main as cli_main
from scheduler.db import open_connection

from tests.fixtures.db_helpers import bootstrap_temp_db
from tests.fixtures.geometry_data import make_square_aoi_feature, write_geojson


class DatabaseBootstrapTests(unittest.TestCase):
	def setUp(self) -> None:
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		self.workspace_dir = Path(self.temp_dir.name)
		self.state_dir = self.workspace_dir / "scheduler_state"
		self.aoi_path = self.workspace_dir / "datasets" / "cerrado_border.geojson"
		write_geojson(self.aoi_path, [make_square_aoi_feature()])
		os.environ["SCHEDULER_WORKSPACE_ROOT"] = str(self.workspace_dir)
		os.environ["SCHEDULER_AOI_PATH"] = str(self.aoi_path)
		self.addCleanup(os.environ.pop, "SCHEDULER_WORKSPACE_ROOT", None)
		self.addCleanup(os.environ.pop, "SCHEDULER_AOI_PATH", None)

	def test_bootstrap_creates_schema(self) -> None:
		db_path = bootstrap_temp_db(self.workspace_dir, self.state_dir)
		self.assertTrue(db_path.exists())
		with closing(sqlite3.connect(db_path)) as connection:
			tables = {
				row[0]
				for row in connection.execute(
					"SELECT name FROM sqlite_master WHERE type='table'"
				).fetchall()
			}
		for expected in {"schema_meta", "dataset_policy", "aois", "runs", "run_lease", "run_steps"}:
			self.assertIn(expected, tables)

	def test_bootstrap_is_idempotent(self) -> None:
		first = cli_main(["--bootstrap-db", "--state-dir", str(self.state_dir)])
		second = cli_main(["--bootstrap-db", "--state-dir", str(self.state_dir)])
		self.assertEqual(first, 0)
		self.assertEqual(second, 0)

	def test_bootstrap_seeds_dataset_policies_once(self) -> None:
		db_path = bootstrap_temp_db(self.workspace_dir, self.state_dir)
		with closing(sqlite3.connect(db_path)) as connection:
			count = connection.execute("SELECT COUNT(*) FROM dataset_policy").fetchone()[0]
		self.assertEqual(count, 3)

	def test_bootstrap_seeds_active_cerrado_aoi(self) -> None:
		db_path = bootstrap_temp_db(self.workspace_dir, self.state_dir)
		with closing(sqlite3.connect(db_path)) as connection:
			row = connection.execute(
				"SELECT aoi_id, aoi_name, is_active FROM aois WHERE aoi_id = ?",
				("cerrado_biome_v1",),
			).fetchone()
		self.assertIsNotNone(row)
		self.assertEqual(row[0], "cerrado_biome_v1")
		self.assertEqual(row[1], "Cerrado")
		self.assertEqual(row[2], 1)

	def test_foreign_keys_enabled(self) -> None:
		db_path = bootstrap_temp_db(self.workspace_dir, self.state_dir)
		with closing(open_connection(db_path)) as connection:
			foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
		self.assertEqual(foreign_keys, 1)


if __name__ == "__main__":
	unittest.main()