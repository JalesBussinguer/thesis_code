from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path

from .models import AppConfig
from .paths import build_paths


def _repo_root_from_package() -> Path:
	return Path(__file__).resolve().parent.parent


def load_config(args: Namespace) -> AppConfig:
	repo_root = Path(os.getenv("SCHEDULER_WORKSPACE_ROOT", _repo_root_from_package()))
	state_dir = Path(args.state_dir).resolve() if getattr(args, "state_dir", None) else None
	db_path = Path(args.db_path).resolve() if getattr(args, "db_path", None) else None
	aoi_override = os.getenv("SCHEDULER_AOI_PATH")
	aoi_path = Path(aoi_override).resolve() if aoi_override else None
	paths = build_paths(
		repo_root=repo_root,
		state_dir=state_dir,
		database_path=db_path,
		aoi_path=aoi_path,
	)
	return AppConfig(
		paths=paths,
		schema_version="1",
		active_aoi_id="cerrado_biome_v1",
		active_aoi_name="Cerrado",
		verbose=bool(getattr(args, "verbose", False)),
	)