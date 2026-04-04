from __future__ import annotations

import json
from pathlib import Path


def make_square_aoi_feature() -> dict:
	return {
		"type": "Feature",
		"properties": {"name": "test_aoi"},
		"geometry": {
			"type": "Polygon",
			"coordinates": [[[-1.0, -1.0], [-1.0, 1.0], [1.0, 1.0], [1.0, -1.0], [-1.0, -1.0]]],
		},
	}


def write_geojson(path: Path, features: list[dict]) -> Path:
	payload = {"type": "FeatureCollection", "features": features}
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(payload), encoding="utf-8")
	return path