from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scheduler.allowlist import classify_allowlist_decision, upsert_allowlist_entry, upsert_orbit_aoi_coverage
from scheduler.baseline import upsert_orbit_baseline
from scheduler.db import open_connection
from scheduler.geometry_aoi import compute_intersection_metrics, load_aoi_geometry
from tests.fixtures.db_helpers import bootstrap_temp_db
from tests.fixtures.geometry_data import make_square_aoi_feature, write_geojson


class AllowlistTests(unittest.TestCase):
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
		self.db_path = bootstrap_temp_db(self.workspace_dir, self.state_dir)
		self.aoi_geometry = load_aoi_geometry(self.aoi_path)

	def test_new_nisar_orbit_inside_is_allowed(self) -> None:
		intersection = compute_intersection_metrics(
			"POLYGON((-0.5 -0.5, -0.5 0.5, 0.5 0.5, 0.5 -0.5, -0.5 -0.5))",
			self.aoi_geometry,
		)
		decision = classify_allowlist_decision("NISAR", intersection)
		self.assertEqual(decision.allow_status, "allowed")

	def test_new_biomass_track_inside_is_allowed(self) -> None:
		intersection = compute_intersection_metrics(
			"POLYGON((-0.5 -0.5, -0.5 0.5, 0.5 0.5, 0.5 -0.5, -0.5 -0.5))",
			self.aoi_geometry,
		)
		decision = classify_allowlist_decision("BIOMASS", intersection)
		self.assertEqual(decision.allow_status, "allowed")

	def test_sentinel1_edge_case_remains_candidate(self) -> None:
		intersection = compute_intersection_metrics(
			"POLYGON((0.9 0.9, 0.9 1.4, 1.4 1.4, 1.4 0.9, 0.9 0.9))",
			self.aoi_geometry,
		)
		decision = classify_allowlist_decision("SENTINEL-1", intersection, observation_count=1)
		self.assertEqual(decision.allow_status, "candidate")

	def test_blocked_orbit_can_become_allowed(self) -> None:
		with closing(open_connection(self.db_path)) as connection:
			upsert_orbit_baseline(connection, "NISAR|NISAR|ASCENDING|117|DHDH", {"dataset": "NISAR", "platform": "NISAR", "flight_direction": "ASCENDING", "path_number": 117, "beam_mode": "DHDH"})
			outside = compute_intersection_metrics("POLYGON((2 2, 2 3, 3 3, 3 2, 2 2))", self.aoi_geometry)
			outside_decision = classify_allowlist_decision("NISAR", outside)
			coverage_id = upsert_orbit_aoi_coverage(connection, "NISAR|NISAR|ASCENDING|117|DHDH", "cerrado_biome_v1", outside, outside_decision)
			upsert_allowlist_entry(connection, "cerrado_biome_v1", "NISAR|NISAR|ASCENDING|117|DHDH", outside_decision, coverage_id)
			inside = compute_intersection_metrics("POLYGON((-0.5 -0.5, -0.5 0.5, 0.5 0.5, 0.5 -0.5, -0.5 -0.5))", self.aoi_geometry)
			inside_decision = classify_allowlist_decision("NISAR", inside)
			coverage_id = upsert_orbit_aoi_coverage(connection, "NISAR|NISAR|ASCENDING|117|DHDH", "cerrado_biome_v1", inside, inside_decision)
			upsert_allowlist_entry(connection, "cerrado_biome_v1", "NISAR|NISAR|ASCENDING|117|DHDH", inside_decision, coverage_id)
			row = connection.execute(
				"SELECT allow_status FROM orbit_download_allowlist WHERE aoi_id = ? AND orbit_scope_key = ?",
				("cerrado_biome_v1", "NISAR|NISAR|ASCENDING|117|DHDH"),
			).fetchone()
		self.assertEqual(row["allow_status"], "allowed")


if __name__ == "__main__":
	unittest.main()