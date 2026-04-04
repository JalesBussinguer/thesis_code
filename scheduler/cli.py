from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .db import bootstrap_db
from .lease import LeaseHeldError
from .main_runner import SchedulerPreconditionError, UnsupportedModeError, run


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Bootstrap and run the AOI-aware scheduler.")
	parser.add_argument("--bootstrap-db", action="store_true", help="Create the scheduler database and seed base state.")
	parser.add_argument(
		"--mode",
		choices=("dry-run", "poll-only", "download-only", "full-run"),
		default="dry-run",
		help="Scheduler execution mode.",
	)
	parser.add_argument("--db-path", help="Optional override for the SQLite database path.")
	parser.add_argument("--state-dir", help="Optional override for the scheduler_state directory.")
	parser.add_argument("--verbose", action="store_true", help="Print additional local execution details.")
	return parser


def main(argv: list[str] | None = None) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)
	config = load_config(args)
	try:
		if args.bootstrap_db:
			summary = bootstrap_db(config)
			print(
				f"Database bootstrap completed: {summary.database_path} | active AOI: "
				f"{summary.active_aoi} | policies: {summary.seeded_policy_count}"
			)
			return 0
		if not Path(config.paths.database_path).exists():
			print("Scheduler database not found. Run: python -m scheduler --bootstrap-db")
			return 3
		run_summary = run(config, mode=args.mode)
		print(
			f"Dry-run completed: run_id={run_summary.run_id} | lease released | "
			"no external queries executed"
		)
		return 0
	except LeaseHeldError as error:
		print(str(error))
		return 4
	except SchedulerPreconditionError as error:
		print(str(error))
		return 3
	except UnsupportedModeError as error:
		print(str(error))
		return 1
	except Exception as error:
		print(f"Scheduler execution failed: {error}")
		return 1