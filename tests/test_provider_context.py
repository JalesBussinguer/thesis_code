from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from shapely.wkt import loads as load_wkt

from scheduler.config import load_config
from scheduler.geometry_aoi import area_square_km, load_aoi_context
from scheduler.providers import build_discovery_context
from tests.fixtures.geometry_data import make_square_aoi_feature, write_geojson


class ProviderContextTests(unittest.TestCase):
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

	def _load_config(self):
		from argparse import Namespace

		return load_config(Namespace(state_dir=str(self.state_dir), db_path=None, verbose=False))

	def test_load_aoi_context_keeps_search_and_validation_separate(self) -> None:
		context = load_aoi_context(self.search_aoi_path, self.validation_aoi_path)
		search_area = area_square_km(load_wkt(context.search_geometry_wkt))
		validation_area = area_square_km(load_wkt(context.validation_geometry_wkt))
		self.assertEqual(len(context.search_areas), 1)
		self.assertGreater(search_area, validation_area)

	def test_provider_discovery_context_exposes_dual_aoi_contract(self) -> None:
		context = build_discovery_context(
			config=self._load_config(),
			dataset="SENTINEL-1",
			window_start_utc="2026-04-04T00:00:00Z",
			window_end_utc="2026-04-05T00:00:00Z",
		)
		self.assertEqual(context.dataset, "SENTINEL-1")
		self.assertEqual(context.aoi_id, "cerrado_biome_v1")
		self.assertEqual(len(context.search_areas), 1)
		self.assertGreater(
			area_square_km(load_wkt(context.search_geometry_wkt)),
			area_square_km(load_wkt(context.validation_geometry_wkt)),
		)


if __name__ == "__main__":
	unittest.main()