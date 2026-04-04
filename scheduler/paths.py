from __future__ import annotations

from pathlib import Path

from .models import SchedulerPaths


def build_paths(
	repo_root: Path,
	state_dir: Path | None = None,
	database_path: Path | None = None,
	aoi_path: Path | None = None,
) -> SchedulerPaths:
	package_root = Path(__file__).resolve().parent
	repo_root = repo_root.resolve()
	resolved_state_dir = (state_dir or (repo_root / "scheduler_state")).resolve()
	resolved_db_path = (database_path or (resolved_state_dir / "catalog_scheduler.db")).resolve()
	resolved_aoi_path = (aoi_path or (repo_root / "datasets" / "cerrado_border.geojson")).resolve()
	return SchedulerPaths(
		repo_root=repo_root,
		state_dir=resolved_state_dir,
		database_path=resolved_db_path,
		logs_dir=resolved_state_dir / "logs",
		reports_dir=resolved_state_dir / "reports",
		exports_dir=resolved_state_dir / "exports",
		schema_path=(package_root / "sql" / "catalog_scheduler_schema.sql").resolve(),
		aoi_path=resolved_aoi_path,
	)


def ensure_state_directories(paths: SchedulerPaths) -> None:
	for directory in (paths.state_dir, paths.logs_dir, paths.reports_dir, paths.exports_dir):
		directory.mkdir(parents=True, exist_ok=True)