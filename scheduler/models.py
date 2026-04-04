from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
	aoi_path: Path


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