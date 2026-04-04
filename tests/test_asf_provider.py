from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scheduler.config import load_config
from scheduler.providers.base import build_discovery_context
from scheduler.providers.asf import build_search_params, record_to_product
from tests.fixtures.geometry_data import make_square_aoi_feature, write_geojson


class AsfProviderTests(unittest.TestCase):
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

	def _config(self):
		from argparse import Namespace

		return load_config(Namespace(state_dir=str(self.state_dir), db_path=None, verbose=False))

	def test_build_search_params_uses_bbox_area_and_orbit_filters(self) -> None:
		context = build_discovery_context(self._config(), "SENTINEL-1", "2026-04-04T00:00:00Z", "2026-04-04T12:00:00Z")
		orbit_row = {
			"processing_level": "SLC",
			"platform": "Sentinel-1A",
			"path_number": 24,
			"frame_number": 603,
			"flight_direction": "DESCENDING",
			"beam_mode": "IW",
		}
		params = build_search_params(context, orbit_row, context.search_areas[0])
		self.assertEqual(params["dataset"], "SENTINEL-1")
		self.assertEqual(params["relativeOrbit"], 24)
		self.assertEqual(params["frame"], 603)
		self.assertIn("intersectsWith", params)

	def test_record_to_product_maps_core_fields(self) -> None:
		record = {
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
		product = record_to_product("SENTINEL-1", record)
		self.assertEqual(product.provider_product_id, "S1_TEST_001")
		self.assertEqual(product.orbit_scope_key, "SENTINEL-1|Sentinel-1A|DESCENDING|24|IW")
		self.assertEqual(product.assets[0].filename, "s1.zip")


if __name__ == "__main__":
	unittest.main()