from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scheduler.geometry_aoi import compute_intersection_metrics, load_aoi_geometry
from tests.fixtures.geometry_data import make_square_aoi_feature, write_geojson


class GeometryAoiTests(unittest.TestCase):
	def setUp(self) -> None:
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		self.aoi_path = Path(self.temp_dir.name) / "aoi.geojson"
		write_geojson(self.aoi_path, [make_square_aoi_feature()])
		self.aoi_geometry = load_aoi_geometry(self.aoi_path)

	def test_inside_footprint_intersects(self) -> None:
		result = compute_intersection_metrics(
			"POLYGON((-0.5 -0.5, -0.5 0.5, 0.5 0.5, 0.5 -0.5, -0.5 -0.5))",
			self.aoi_geometry,
		)
		self.assertTrue(result.intersects)
		self.assertGreater(result.intersection_fraction, 0.9)

	def test_outside_footprint_does_not_intersect(self) -> None:
		result = compute_intersection_metrics(
			"POLYGON((2 2, 2 3, 3 3, 3 2, 2 2))",
			self.aoi_geometry,
		)
		self.assertFalse(result.intersects)
		self.assertEqual(result.intersection_fraction, 0.0)

	def test_edge_footprint_has_small_intersection(self) -> None:
		result = compute_intersection_metrics(
			"POLYGON((0.9 0.9, 0.9 1.4, 1.4 1.4, 1.4 0.9, 0.9 0.9))",
			self.aoi_geometry,
		)
		self.assertTrue(result.intersects)
		self.assertLess(result.intersection_fraction, 0.1)

	def test_invalid_footprint_is_normalized(self) -> None:
		result = compute_intersection_metrics(
			"POLYGON((0 0, 1 1, 1 0, 0 1, 0 0))",
			self.aoi_geometry,
		)
		self.assertGreaterEqual(result.intersection_fraction, 0.0)


if __name__ == "__main__":
	unittest.main()