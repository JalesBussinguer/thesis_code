from __future__ import annotations

from pathlib import Path

from scheduler.cli import main as cli_main

from .geometry_data import make_square_aoi_feature, write_geojson


def bootstrap_temp_db(workspace_dir: Path, state_dir: Path) -> Path:
	aoi_path = workspace_dir / "datasets" / "cerrado_border.geojson"
	write_geojson(aoi_path, [make_square_aoi_feature()])
	import os

	os.environ["SCHEDULER_WORKSPACE_ROOT"] = str(workspace_dir)
	os.environ["SCHEDULER_AOI_PATH"] = str(aoi_path)
	exit_code = cli_main(["--bootstrap-db", "--state-dir", str(state_dir)])
	if exit_code != 0:
		raise RuntimeError(f"Bootstrap failed with exit code {exit_code}")
	return state_dir / "catalog_scheduler.db"