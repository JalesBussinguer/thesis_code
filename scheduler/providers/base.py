from __future__ import annotations

from ..geometry_aoi import load_aoi_context
from ..models import AppConfig, ProviderDiscoveryContext


def build_discovery_context(
	config: AppConfig,
	dataset: str,
	window_start_utc: str,
	window_end_utc: str,
) -> ProviderDiscoveryContext:
	aoi_context = load_aoi_context(
		search_aoi_path=config.paths.search_aoi_path,
		validation_aoi_path=config.paths.validation_aoi_path,
	)
	return ProviderDiscoveryContext(
		dataset=dataset,
		aoi_id=config.active_aoi_id,
		window_start_utc=window_start_utc,
		window_end_utc=window_end_utc,
		search_areas=aoi_context.search_areas,
		search_geometry_wkt=aoi_context.search_geometry_wkt,
		validation_geometry_wkt=aoi_context.validation_geometry_wkt,
	)