from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCHEDULER_VERSION = "0.1.0"
MAIN_LEASE_NAME = "main_scheduler"
DEFAULT_LEASE_TTL_SECONDS = 600


@dataclass(frozen=True)
class SchedulerPaths:
	repo_root: Path
	state_dir: Path
	database_path: Path
	logs_dir: Path
	reports_dir: Path
	exports_dir: Path
	schema_path: Path
	search_aoi_path: Path
	validation_aoi_path: Path


@dataclass(frozen=True)
class AppConfig:
	paths: SchedulerPaths
	schema_version: str
	active_aoi_id: str
	active_aoi_name: str
	verbose: bool = False


@dataclass(frozen=True)
class BootstrapSummary:
	database_path: Path
	schema_version: str
	active_aoi: str
	seeded_policy_count: int


@dataclass(frozen=True)
class RunSummary:
	run_id: str
	mode: str
	lease_name: str


@dataclass(frozen=True)
class AssetRecord:
	asset_key: str
	source_url: str
	filename: str
	size_mb: float | None = None
	checksum_hint: str | None = None
	is_required: bool = True
	asset_type: str = "primary"


@dataclass(frozen=True)
class ProductRecord:
	dataset: str
	provider_product_id: str
	orbit_scope_key: str
	scene_name: str | None = None
	item_id: str | None = None
	platform: str | None = None
	processing_level: str | None = None
	relative_orbit: int | None = None
	absolute_orbit: int | None = None
	path_number: int | None = None
	frame_number: int | None = None
	beam_mode: str | None = None
	flight_direction: str | None = None
	acquisition_start_utc: str | None = None
	acquisition_stop_utc: str | None = None
	footprint_wkt: str | None = None
	metadata_json: dict[str, Any] = field(default_factory=dict)
	assets: tuple[AssetRecord, ...] = ()


@dataclass(frozen=True)
class IntersectionResult:
	intersects: bool
	intersection_area_km2: float
	intersection_fraction: float
	footprint_area_km2: float
	intersection_wkt: str | None = None


@dataclass(frozen=True)
class AllowlistDecision:
	coverage_status: str
	allow_status: str
	allow_reason: str
	auto_discovered: bool


@dataclass(frozen=True)
class SearchArea:
	query_name: str
	feature_index: int | None
	geometry_wkt: str
	geometry_geojson: dict[str, Any]


@dataclass(frozen=True)
class AoiContext:
	search_areas: tuple[SearchArea, ...]
	search_geometry_wkt: str
	validation_geometry_wkt: str


@dataclass(frozen=True)
class ProviderDiscoveryContext:
	dataset: str
	aoi_id: str
	window_start_utc: str
	window_end_utc: str
	search_areas: tuple[SearchArea, ...]
	search_geometry_wkt: str
	validation_geometry_wkt: str