from __future__ import annotations

import json
from pathlib import Path


def make_square_aoi_feature(min_x: float = -1.0, min_y: float = -1.0, max_x: float = 1.0, max_y: float = 1.0) -> dict:
	return {
		"type": "Feature",
		"properties": {"name": "test_aoi"},
		"geometry": {
			"type": "Polygon",
			"coordinates": [[[min_x, min_y], [min_x, max_y], [max_x, max_y], [max_x, min_y], [min_x, min_y]]],
		},
	}


def write_geojson(path: Path, features: list[dict]) -> Path:
	payload = {"type": "FeatureCollection", "features": features}
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(payload), encoding="utf-8")
	return path