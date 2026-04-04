from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scheduler.baseline import upsert_orbit_baseline
from scheduler.db import open_connection
from scheduler.reconciliation import upsert_product, upsert_product_assets
from tests.fixtures.db_helpers import bootstrap_temp_db
from tests.fixtures.geometry_data import make_square_aoi_feature, write_geojson
from tests.fixtures.product_records import make_nisar_record_inside_new_orbit, make_sentinel1_record_inside


class ReconciliationTests(unittest.TestCase):
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

	def test_same_product_is_upserted_without_duplication(self) -> None:
		record = make_sentinel1_record_inside()
		with closing(open_connection(self.db_path)) as connection:
			upsert_orbit_baseline(connection, record.orbit_scope_key, {**record.__dict__, "dataset": record.dataset})
			upsert_product(connection, "cerrado_biome_v1", record)
			upsert_product(connection, "cerrado_biome_v1", record)
			count = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
		self.assertEqual(count, 1)

	def test_same_asset_is_upserted_without_duplication(self) -> None:
		record = make_nisar_record_inside_new_orbit()
		with closing(open_connection(self.db_path)) as connection:
			upsert_orbit_baseline(connection, record.orbit_scope_key, {**record.__dict__, "dataset": record.dataset})
			product_uid = upsert_product(connection, "cerrado_biome_v1", record)
			upsert_product_assets(connection, product_uid, record.assets)
			upsert_product_assets(connection, product_uid, record.assets)
			count = connection.execute("SELECT COUNT(*) FROM product_assets").fetchone()[0]
		self.assertEqual(count, 2)

	def test_product_inside_aoi_becomes_eligible(self) -> None:
		record = make_sentinel1_record_inside()
		with closing(open_connection(self.db_path)) as connection:
			upsert_orbit_baseline(connection, record.orbit_scope_key, {**record.__dict__, "dataset": record.dataset})
			product_uid = upsert_product(connection, "cerrado_biome_v1", record, intersects_aoi=True, intersection_fraction=1.0)
			row = connection.execute("SELECT current_status FROM products WHERE product_uid = ?", (product_uid,)).fetchone()
		self.assertEqual(row["current_status"], "eligible")


if __name__ == "__main__":
	unittest.main()