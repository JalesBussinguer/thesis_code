from __future__ import annotations

from pathlib import Path

from scheduler.cli import main as cli_main

from .geometry_data import make_square_aoi_feature, write_geojson


def bootstrap_temp_db(workspace_dir: Path, state_dir: Path) -> Path:
	search_aoi_path = workspace_dir / "datasets" / "cerrado_bbox.geojson"
	validation_aoi_path = workspace_dir / "datasets" / "cerrado_border.geojson"
	write_geojson(search_aoi_path, [make_square_aoi_feature(min_x=-2.0, min_y=-2.0, max_x=2.0, max_y=2.0)])
	write_geojson(validation_aoi_path, [make_square_aoi_feature()])
	import os

	os.environ["SCHEDULER_WORKSPACE_ROOT"] = str(workspace_dir)
	os.environ["SCHEDULER_SEARCH_AOI_PATH"] = str(search_aoi_path)
	os.environ["SCHEDULER_VALIDATION_AOI_PATH"] = str(validation_aoi_path)
	exit_code = cli_main(["--bootstrap-db", "--state-dir", str(state_dir)])
	if exit_code != 0:
		raise RuntimeError(f"Bootstrap failed with exit code {exit_code}")
	return state_dir / "catalog_scheduler.db"