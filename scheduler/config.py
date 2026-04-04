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
	credentials_override = os.getenv("SCHEDULER_CREDENTIALS_PATH")
	search_aoi_override = os.getenv("SCHEDULER_SEARCH_AOI_PATH")
	validation_aoi_override = os.getenv("SCHEDULER_VALIDATION_AOI_PATH") or os.getenv("SCHEDULER_AOI_PATH")
	credentials_path = Path(credentials_override).resolve() if credentials_override else None
	search_aoi_path = Path(search_aoi_override).resolve() if search_aoi_override else None
	validation_aoi_path = Path(validation_aoi_override).resolve() if validation_aoi_override else None
	paths = build_paths(
		repo_root=repo_root,
		state_dir=state_dir,
		database_path=db_path,
		credentials_path=credentials_path,
		search_aoi_path=search_aoi_path,
		validation_aoi_path=validation_aoi_path,
	)
	return AppConfig(
		paths=paths,
		schema_version="1",
		active_aoi_id="cerrado_biome_v1",
		active_aoi_name="Cerrado",
		verbose=bool(getattr(args, "verbose", False)),
	)