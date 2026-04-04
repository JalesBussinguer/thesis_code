from __future__ import annotations

from .db import open_connection
from .downloads import execute_download_only
from .geometry_aoi import load_aoi_context
from .lease import LeaseHeldError, acquire_main_lease, release_main_lease
from .models import MAIN_LEASE_NAME, RunSummary
from .polling import execute_poll_only
from .reporting import export_run_report
from .runs import close_step_failure, close_step_success, create_run, finalize_run, open_step


class SchedulerPreconditionError(RuntimeError):
	"""Raised when the scheduler cannot start due to missing prerequisites."""


class UnsupportedModeError(RuntimeError):
	"""Raised when a mode is accepted by the CLI but not implemented yet."""


def run(config, mode: str, request_get=None, downloader=None) -> RunSummary:
	if mode not in {"dry-run", "poll-only", "download-only", "full-run"}:
		raise UnsupportedModeError(f"Mode '{mode}' is not implemented yet. Use --mode dry-run, --mode poll-only, --mode download-only or --mode full-run.")
	if not config.paths.database_path.exists():
		raise SchedulerPreconditionError("Scheduler database not found. Run: python -m scheduler --bootstrap-db")

	connection = open_connection(config.paths.database_path)
	try:
		run_id = create_run(connection, mode=mode)
		lease_step_id = open_step(connection, run_id, "acquire_lease")
		try:
			acquire_main_lease(connection, run_id)
			close_step_success(connection, lease_step_id)
			load_state_step_id = open_step(connection, run_id, "load_state")
			load_aoi_context(
				search_aoi_path=config.paths.search_aoi_path,
				validation_aoi_path=config.paths.validation_aoi_path,
			)
			close_step_success(connection, load_state_step_id)
			if mode in {"poll-only", "full-run"}:
				plan_step_id = open_step(connection, run_id, "execute_polling")
				poll_summary = execute_poll_only(connection, config, run_id, request_get=request_get)
				close_step_success(
					connection,
					plan_step_id,
					records_in=poll_summary["candidates"],
					records_out=poll_summary["products_upserted"],
				)
			if mode in {"download-only", "full-run"}:
				download_step_id = open_step(connection, run_id, "execute_downloads")
				download_summary = execute_download_only(connection, config, run_id, downloader=downloader)
				close_step_success(
					connection,
					download_step_id,
					records_in=download_summary["queued_assets"],
					records_out=download_summary["downloaded_assets"],
				)
			report_step_id = open_step(connection, run_id, "export_reports")
			report_outputs = export_run_report(connection, config, run_id)
			close_step_success(connection, report_step_id, records_out=len(report_outputs))
		except Exception as error:
			close_step_failure(connection, lease_step_id, error)
			finalize_run(connection, run_id, "failed", 1, str(error))
			raise
		finally:
			release_step_id = open_step(connection, run_id, "release_lease")
			try:
				release_main_lease(connection, run_id)
				close_step_success(connection, release_step_id)
			except Exception as release_error:
				close_step_failure(connection, release_step_id, release_error)
				if not _run_failed(connection, run_id):
					finalize_run(connection, run_id, "partial", 1, str(release_error))
				raise
		if _run_failed(connection, run_id):
			raise LeaseHeldError("Run failed before releasing the lease")
		note = (
			"Dry-run completed without external queries"
			if mode == "dry-run"
			else "Poll-only completed" if mode == "poll-only" else "Download-only completed" if mode == "download-only" else "Full-run completed"
		)
		finalize_run(connection, run_id, "succeeded", 0, note)
		return RunSummary(run_id=run_id, mode=mode, lease_name=MAIN_LEASE_NAME)
	finally:
		connection.close()


def _run_failed(connection, run_id: str) -> bool:
	row = connection.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
	return row is not None and row["status"] == "failed"