from __future__ import annotations

from scheduler.models import AssetRecord, ProductRecord


def make_sentinel1_record_inside() -> ProductRecord:
	return ProductRecord(
		dataset="SENTINEL-1",
		provider_product_id="S1_TEST_001",
		scene_name="S1_TEST_SCENE_001",
		platform="Sentinel-1A",
		processing_level="SLC",
		orbit_scope_key="SENTINEL-1|Sentinel-1A|DESCENDING|24|IW",
		relative_orbit=24,
		absolute_orbit=62024,
		path_number=24,
		frame_number=603,
		beam_mode="IW",
		flight_direction="DESCENDING",
		acquisition_start_utc="2026-03-01T08:41:37Z",
		acquisition_stop_utc="2026-03-01T08:46:52Z",
		footprint_wkt="POLYGON((-0.5 -0.5, -0.5 0.5, 0.5 0.5, 0.5 -0.5, -0.5 -0.5))",
		metadata_json={"source": "test"},
		assets=(AssetRecord(asset_key="primary", source_url="https://example.com/s1.zip", filename="s1.zip"),),
	)


def make_sentinel1_record_edge() -> ProductRecord:
	record = make_sentinel1_record_inside()
	return ProductRecord(
		**{**record.__dict__, "provider_product_id": "S1_TEST_002", "footprint_wkt": "POLYGON((0.9 0.9, 0.9 1.4, 1.4 1.4, 1.4 0.9, 0.9 0.9))"}
	)


def make_nisar_record_inside_new_orbit() -> ProductRecord:
	return ProductRecord(
		dataset="NISAR",
		provider_product_id="NISAR_TEST_001",
		scene_name="NISAR_TEST_SCENE_001",
		platform="NISAR",
		processing_level="RSLC",
		orbit_scope_key="NISAR|NISAR|ASCENDING|117|DHDH",
		path_number=117,
		frame_number=176,
		beam_mode="DHDH",
		flight_direction="ASCENDING",
		acquisition_start_utc="2026-01-17T08:48:10Z",
		acquisition_stop_utc="2026-01-17T08:48:42Z",
		footprint_wkt="POLYGON((-0.5 -0.5, -0.5 0.5, 0.5 0.5, 0.5 -0.5, -0.5 -0.5))",
		metadata_json={"source": "test"},
		assets=(
			AssetRecord(asset_key="primary", source_url="https://example.com/nisar.h5", filename="nisar.h5"),
			AssetRecord(asset_key="kml", source_url="https://example.com/nisar.kml", filename="nisar.kml", is_required=False, asset_type="kml"),
		),
	)


def make_biomass_record_inside_new_track() -> ProductRecord:
	return ProductRecord(
		dataset="BIOMASS",
		provider_product_id="BIO_TEST_001",
		item_id="BIO_TEST_001",
		scene_name="BIO_S1_SCS__1S_20260301T094741_20260301T094802_T_G01_M01_C02_T006_F289_01_TEST",
		platform="BIOMASS",
		processing_level="BiomassLevel1a",
		orbit_scope_key="BIOMASS|SCS|ASCENDING|6|289",
		absolute_orbit=5022,
		track_number=6,
		frame_number=289,
		flight_direction="ASCENDING",
		acquisition_start_utc="2026-03-01T09:47:41Z",
		acquisition_stop_utc="2026-03-01T09:48:02Z",
		footprint_wkt="POLYGON((-0.5 -0.5, -0.5 0.5, 0.5 0.5, 0.5 -0.5, -0.5 -0.5))",
		metadata_json={"source": "test"},
		assets=(AssetRecord(asset_key="product", source_url="https://example.com/biomass.zip", filename="biomass.zip"),),
	)