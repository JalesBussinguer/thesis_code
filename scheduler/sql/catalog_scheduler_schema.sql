PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;

BEGIN;

CREATE TABLE IF NOT EXISTS schema_meta (
	name TEXT PRIMARY KEY,
	value TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_policy (
	policy_id TEXT PRIMARY KEY,
	dataset TEXT NOT NULL,
	policy_version INTEGER NOT NULL,
	enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
	priority INTEGER NOT NULL DEFAULT 100,
	prediction_mode TEXT NOT NULL CHECK (prediction_mode IN ('observational', 'hybrid', 'poll_only')),
	pre_window_hours INTEGER NOT NULL CHECK (pre_window_hours >= 0),
	active_window_hours INTEGER NOT NULL CHECK (active_window_hours >= 0),
	retry_backoff_hours_json TEXT NOT NULL,
	stale_after_hours INTEGER NOT NULL CHECK (stale_after_hours >= 0),
	max_retry_count INTEGER NOT NULL CHECK (max_retry_count >= 0),
	query_margin_hours INTEGER NOT NULL CHECK (query_margin_hours >= 0),
	download_enabled INTEGER NOT NULL CHECK (download_enabled IN (0, 1)),
	min_confidence REAL NOT NULL CHECK (min_confidence >= 0.0 AND min_confidence <= 1.0),
	notes TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL,
	UNIQUE(dataset, policy_version)
);

CREATE TABLE IF NOT EXISTS runs (
	run_id TEXT PRIMARY KEY,
	trigger_type TEXT NOT NULL CHECK (trigger_type IN ('manual', 'windows_scheduler', 'recovery', 'test')),
	mode TEXT NOT NULL CHECK (mode IN ('dry_run', 'poll_only', 'download_only', 'full_run')),
	host TEXT NOT NULL,
	pid INTEGER,
	started_at_utc TEXT NOT NULL,
	ended_at_utc TEXT,
	status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'partial', 'aborted')),
	exit_code INTEGER,
	scheduler_version TEXT NOT NULL,
	dry_run INTEGER NOT NULL CHECK (dry_run IN (0, 1)),
	config_hash TEXT NOT NULL,
	notes TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_lease (
	lease_name TEXT PRIMARY KEY,
	owner_run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
	acquired_at_utc TEXT NOT NULL,
	heartbeat_at_utc TEXT NOT NULL,
	expires_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_steps (
	run_step_id TEXT PRIMARY KEY,
	run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
	step_name TEXT NOT NULL,
	started_at_utc TEXT NOT NULL,
	ended_at_utc TEXT,
	status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
	checkpoint_token TEXT,
	records_in INTEGER,
	records_out INTEGER,
	error_class TEXT,
	error_message TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL,
	UNIQUE(run_id, step_name)
);

CREATE TABLE IF NOT EXISTS aois (
	aoi_id TEXT PRIMARY KEY,
	aoi_name TEXT NOT NULL,
	aoi_type TEXT NOT NULL CHECK (aoi_type IN ('biome', 'bbox', 'custom')),
	geometry_path TEXT NOT NULL,
	geometry_hash TEXT NOT NULL,
	crs TEXT NOT NULL,
	is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL,
	UNIQUE(aoi_name, geometry_hash)
);

CREATE TABLE IF NOT EXISTS orbit_baseline (
	orbit_scope_key TEXT PRIMARY KEY,
	dataset TEXT NOT NULL,
	platform TEXT,
	flight_direction TEXT,
	path_number INTEGER,
	frame_number INTEGER,
	beam_mode TEXT,
	mode TEXT,
	orbit_state TEXT,
	track_number INTEGER,
	frame_code TEXT,
	first_seen_acquisition_utc TEXT,
	last_seen_acquisition_utc TEXT,
	median_gap_hours REAL,
	p90_gap_hours REAL,
	historical_scene_count INTEGER NOT NULL DEFAULT 0 CHECK (historical_scene_count >= 0),
	confidence_score REAL NOT NULL CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
	last_calibrated_at_utc TEXT NOT NULL,
	baseline_source TEXT NOT NULL CHECK (baseline_source IN ('historical_csv', 'live_inventory', 'mixed')),
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orbit_aoi_coverage (
	coverage_id TEXT PRIMARY KEY,
	orbit_scope_key TEXT NOT NULL REFERENCES orbit_baseline(orbit_scope_key) ON DELETE CASCADE,
	aoi_id TEXT NOT NULL REFERENCES aois(aoi_id) ON DELETE CASCADE,
	coverage_status TEXT NOT NULL CHECK (coverage_status IN ('intersects', 'outside', 'unknown', 'candidate_new')),
	intersection_area_km2 REAL,
	intersection_fraction REAL CHECK (intersection_fraction IS NULL OR (intersection_fraction >= 0.0 AND intersection_fraction <= 1.0)),
	footprint_count_used INTEGER NOT NULL DEFAULT 0 CHECK (footprint_count_used >= 0),
	first_confirmed_at_utc TEXT,
	last_confirmed_at_utc TEXT,
	last_checked_at_utc TEXT NOT NULL,
	source_run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
	geometry_evidence_path TEXT,
	notes TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL,
	UNIQUE(orbit_scope_key, aoi_id)
);

CREATE TABLE IF NOT EXISTS orbit_download_allowlist (
	allowlist_id TEXT PRIMARY KEY,
	aoi_id TEXT NOT NULL REFERENCES aois(aoi_id) ON DELETE CASCADE,
	orbit_scope_key TEXT NOT NULL REFERENCES orbit_baseline(orbit_scope_key) ON DELETE CASCADE,
	allow_status TEXT NOT NULL CHECK (allow_status IN ('allowed', 'blocked', 'candidate', 'revoked')),
	allow_reason TEXT NOT NULL CHECK (allow_reason IN ('intersects_confirmed', 'manual_override', 'new_track_candidate', 'outside_aoi', 'revoked_by_policy')),
	auto_discovered INTEGER NOT NULL CHECK (auto_discovered IN (0, 1)),
	first_added_at_utc TEXT NOT NULL,
	last_reviewed_at_utc TEXT NOT NULL,
	source_coverage_id TEXT REFERENCES orbit_aoi_coverage(coverage_id) ON DELETE SET NULL,
	source_run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
	notes TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL,
	UNIQUE(aoi_id, orbit_scope_key)
);

CREATE TABLE IF NOT EXISTS predicted_events (
	predicted_event_id TEXT PRIMARY KEY,
	dataset TEXT NOT NULL,
	aoi_id TEXT NOT NULL REFERENCES aois(aoi_id) ON DELETE CASCADE,
	orbit_scope_key TEXT NOT NULL REFERENCES orbit_baseline(orbit_scope_key) ON DELETE CASCADE,
	policy_id TEXT NOT NULL REFERENCES dataset_policy(policy_id) ON DELETE RESTRICT,
	predicted_acquisition_utc TEXT NOT NULL,
	availability_start_utc TEXT NOT NULL,
	availability_end_utc TEXT NOT NULL,
	confidence_score REAL NOT NULL CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
	uncertainty_hours REAL NOT NULL CHECK (uncertainty_hours >= 0.0),
	historical_gap_hours REAL,
	derived_from_baseline_at_utc TEXT NOT NULL,
	status TEXT NOT NULL CHECK (status IN ('predicted', 'active', 'satisfied', 'missed', 'stale', 'superseded')),
	superseded_by_event_id TEXT REFERENCES predicted_events(predicted_event_id) ON DELETE SET NULL,
	last_evaluated_at_utc TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL,
	UNIQUE(dataset, aoi_id, orbit_scope_key, predicted_acquisition_utc, policy_id)
);

CREATE TABLE IF NOT EXISTS query_windows (
	query_window_id TEXT PRIMARY KEY,
	dataset TEXT NOT NULL,
	aoi_id TEXT NOT NULL REFERENCES aois(aoi_id) ON DELETE CASCADE,
	orbit_scope_key TEXT NOT NULL REFERENCES orbit_baseline(orbit_scope_key) ON DELETE CASCADE,
	window_start_utc TEXT NOT NULL,
	window_end_utc TEXT NOT NULL,
	window_role TEXT NOT NULL CHECK (window_role IN ('pre_window', 'active_window', 'retry_window', 'stale_window', 'catchup_window', 'manual_window', 'discovery_window')),
	planned_at_utc TEXT NOT NULL,
	executed_in_run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
	status TEXT NOT NULL CHECK (status IN ('planned', 'executed', 'empty', 'results_found', 'failed', 'ambiguous')),
	retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
	next_retry_at_utc TEXT,
	response_fingerprint TEXT,
	result_count INTEGER,
	error_class TEXT,
	error_message TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL,
	UNIQUE(dataset, aoi_id, orbit_scope_key, window_start_utc, window_end_utc, window_role)
);

CREATE TABLE IF NOT EXISTS poll_queue (
	queue_item_id TEXT PRIMARY KEY,
	dataset TEXT NOT NULL,
	aoi_id TEXT NOT NULL REFERENCES aois(aoi_id) ON DELETE CASCADE,
	orbit_scope_key TEXT NOT NULL REFERENCES orbit_baseline(orbit_scope_key) ON DELETE CASCADE,
	predicted_event_id TEXT REFERENCES predicted_events(predicted_event_id) ON DELETE SET NULL,
	query_window_id TEXT NOT NULL REFERENCES query_windows(query_window_id) ON DELETE CASCADE,
	scheduled_for_utc TEXT NOT NULL,
	queue_state TEXT NOT NULL CHECK (queue_state IN ('pending', 'claimed', 'completed', 'retry_scheduled', 'stale', 'cancelled')),
	priority INTEGER NOT NULL DEFAULT 100,
	reason TEXT NOT NULL,
	created_at_utc TEXT NOT NULL,
	claimed_by_run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
	claimed_at_utc TEXT,
	finished_at_utc TEXT,
	updated_at_utc TEXT NOT NULL,
	UNIQUE(query_window_id)
);

CREATE TABLE IF NOT EXISTS products (
	product_uid TEXT PRIMARY KEY,
	dataset TEXT NOT NULL,
	aoi_id TEXT REFERENCES aois(aoi_id) ON DELETE SET NULL,
	provider_product_id TEXT NOT NULL,
	scene_name TEXT,
	item_id TEXT,
	platform TEXT,
	processing_level TEXT,
	orbit_scope_key TEXT NOT NULL REFERENCES orbit_baseline(orbit_scope_key) ON DELETE CASCADE,
	relative_orbit INTEGER,
	absolute_orbit INTEGER,
	path_number INTEGER,
	frame_number INTEGER,
	beam_mode TEXT,
	flight_direction TEXT,
	acquisition_start_utc TEXT,
	acquisition_stop_utc TEXT,
	first_detected_at_utc TEXT NOT NULL,
	last_detected_at_utc TEXT NOT NULL,
	first_query_window_id TEXT REFERENCES query_windows(query_window_id) ON DELETE SET NULL,
	intersects_aoi INTEGER CHECK (intersects_aoi IN (0, 1)),
	intersection_fraction REAL CHECK (intersection_fraction IS NULL OR (intersection_fraction >= 0.0 AND intersection_fraction <= 1.0)),
	coverage_id TEXT REFERENCES orbit_aoi_coverage(coverage_id) ON DELETE SET NULL,
	current_status TEXT NOT NULL CHECK (current_status IN ('discovered', 'eligible', 'queued_for_download', 'downloaded', 'quarantined', 'ignored')),
	metadata_json TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL,
	UNIQUE(dataset, provider_product_id)
);

CREATE TABLE IF NOT EXISTS product_assets (
	asset_uid TEXT PRIMARY KEY,
	product_uid TEXT NOT NULL REFERENCES products(product_uid) ON DELETE CASCADE,
	asset_key TEXT NOT NULL,
	source_url TEXT NOT NULL,
	filename TEXT NOT NULL,
	size_mb REAL,
	checksum_hint TEXT,
	local_path TEXT,
	asset_status TEXT NOT NULL CHECK (asset_status IN ('discovered', 'queued', 'downloading', 'downloaded', 'failed', 'quarantined', 'skipped')),
	first_detected_at_utc TEXT NOT NULL,
	first_download_attempt_at_utc TEXT,
	last_download_attempt_at_utc TEXT,
	completed_at_utc TEXT,
	integrity_status TEXT NOT NULL CHECK (integrity_status IN ('unchecked', 'ok', 'suspicious', 'failed')),
	integrity_notes TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL,
	UNIQUE(product_uid, asset_key)
);

CREATE TABLE IF NOT EXISTS downloads (
	download_id TEXT PRIMARY KEY,
	run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
	asset_uid TEXT NOT NULL REFERENCES product_assets(asset_uid) ON DELETE CASCADE,
	destination_path TEXT NOT NULL,
	temp_path TEXT,
	started_at_utc TEXT NOT NULL,
	ended_at_utc TEXT,
	bytes_expected INTEGER CHECK (bytes_expected IS NULL OR bytes_expected >= 0),
	bytes_written INTEGER CHECK (bytes_written IS NULL OR bytes_written >= 0),
	status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled', 'skipped')),
	http_status INTEGER,
	error_class TEXT,
	error_message TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_integrity_checks (
	integrity_check_id TEXT PRIMARY KEY,
	asset_uid TEXT NOT NULL REFERENCES product_assets(asset_uid) ON DELETE CASCADE,
	run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
	checked_at_utc TEXT NOT NULL,
	check_type TEXT NOT NULL CHECK (check_type IN ('exists', 'size_nonzero', 'size_matches_header', 'extension_expected', 'zip_open_test', 'custom')),
	check_result TEXT NOT NULL CHECK (check_result IN ('passed', 'failed', 'warning')),
	expected_value TEXT,
	observed_value TEXT,
	notes TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_observations (
	observation_id TEXT PRIMARY KEY,
	query_window_id TEXT NOT NULL REFERENCES query_windows(query_window_id) ON DELETE CASCADE,
	run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
	dataset TEXT NOT NULL,
	endpoint_name TEXT NOT NULL CHECK (endpoint_name IN ('asf_search', 'esa_maap_stac', 'earthdata_download', 'esa_maap_download', 'iam_token')),
	request_fingerprint TEXT NOT NULL,
	observed_at_utc TEXT NOT NULL,
	http_status INTEGER,
	parsed_record_count INTEGER,
	observation_status TEXT NOT NULL CHECK (observation_status IN ('ok', 'empty', 'partial', 'failed', 'ambiguous')),
	anomaly_flag INTEGER NOT NULL CHECK (anomaly_flag IN (0, 1)),
	raw_payload_path TEXT,
	notes TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS anomalies (
	anomaly_id TEXT PRIMARY KEY,
	run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
	aoi_id TEXT REFERENCES aois(aoi_id) ON DELETE SET NULL,
	dataset TEXT NOT NULL,
	entity_type TEXT NOT NULL CHECK (entity_type IN ('query_window', 'product', 'asset', 'download', 'prediction', 'api_observation', 'run', 'allowlist', 'coverage')),
	entity_id TEXT NOT NULL,
	severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'critical')),
	anomaly_type TEXT NOT NULL,
	detected_at_utc TEXT NOT NULL,
	status TEXT NOT NULL CHECK (status IN ('open', 'acknowledged', 'resolved', 'ignored')),
	details_json TEXT,
	created_at_utc TEXT NOT NULL,
	updated_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dataset_policy_dataset_enabled ON dataset_policy(dataset, enabled);
CREATE INDEX IF NOT EXISTS idx_runs_status_started_at ON runs(status, started_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_run_steps_run_id ON run_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_aois_active ON aois(is_active);
CREATE INDEX IF NOT EXISTS idx_orbit_baseline_dataset_confidence ON orbit_baseline(dataset, confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_orbit_aoi_coverage_aoi_status ON orbit_aoi_coverage(aoi_id, coverage_status);
CREATE INDEX IF NOT EXISTS idx_orbit_download_allowlist_aoi_status ON orbit_download_allowlist(aoi_id, allow_status);
CREATE INDEX IF NOT EXISTS idx_predicted_events_aoi_status ON predicted_events(aoi_id, status);
CREATE INDEX IF NOT EXISTS idx_predicted_events_aoi_window_start ON predicted_events(aoi_id, availability_start_utc);
CREATE INDEX IF NOT EXISTS idx_query_windows_aoi_status ON query_windows(aoi_id, status);
CREATE INDEX IF NOT EXISTS idx_query_windows_next_retry ON query_windows(next_retry_at_utc);
CREATE INDEX IF NOT EXISTS idx_poll_queue_aoi_state_schedule ON poll_queue(aoi_id, queue_state, scheduled_for_utc);
CREATE INDEX IF NOT EXISTS idx_products_aoi_intersects ON products(aoi_id, intersects_aoi, first_detected_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_products_aoi_status ON products(aoi_id, current_status);
CREATE INDEX IF NOT EXISTS idx_product_assets_status ON product_assets(asset_status);
CREATE INDEX IF NOT EXISTS idx_downloads_status_started ON downloads(status, started_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_api_observations_run ON api_observations(run_id);
CREATE INDEX IF NOT EXISTS idx_anomalies_aoi_detected ON anomalies(aoi_id, detected_at_utc DESC);

COMMIT;