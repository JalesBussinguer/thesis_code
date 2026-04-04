from __future__ import annotations

import unittest

from scheduler.orbit_keys import build_asset_uid, build_orbit_scope_key, build_product_uid


class OrbitKeyTests(unittest.TestCase):
	def test_sentinel1_orbit_key_is_deterministic(self) -> None:
		record = {
			"platform": "Sentinel-1A",
			"flight_direction": "DESCENDING",
			"path_number": 24,
			"beam_mode": "IW",
		}
		first = build_orbit_scope_key("SENTINEL-1", record)
		second = build_orbit_scope_key("SENTINEL-1", record)
		self.assertEqual(first, "SENTINEL-1|Sentinel-1A|DESCENDING|24|IW")
		self.assertEqual(first, second)

	def test_nisar_key_handles_missing_optional_fields(self) -> None:
		record = {"platform": "NISAR", "flight_direction": "ASCENDING", "path_number": 117}
		self.assertEqual(build_orbit_scope_key("NISAR", record), "NISAR|NISAR|ASCENDING|117|NA")

	def test_biomass_key_uses_mode_track_frame(self) -> None:
		record = {"mode": "SCS", "orbit_state": "ASCENDING", "track_number": 6, "frame_number": 289}
		self.assertEqual(build_orbit_scope_key("BIOMASS", record), "BIOMASS|SCS|ASCENDING|6|289")

	def test_product_uid_depends_on_provider_id(self) -> None:
		first = build_product_uid("NISAR", {"provider_product_id": "A"})
		second = build_product_uid("NISAR", {"provider_product_id": "B"})
		self.assertNotEqual(first, second)

	def test_asset_uid_differs_per_asset_key(self) -> None:
		product_uid = build_product_uid("NISAR", {"provider_product_id": "A"})
		self.assertNotEqual(build_asset_uid(product_uid, "primary"), build_asset_uid(product_uid, "kml"))


if __name__ == "__main__":
	unittest.main()