from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import requests
from requests import Session


ESA_MAAP_TOKEN_URL = "https://iam.maap.eo.esa.int/realms/esa-maap/protocol/openid-connect/token"
ESA_MAAP_DEFAULT_CLIENT_ID = "offline-token"
ESA_MAAP_DEFAULT_CLIENT_SECRET = "p1eL7uonXs6MDxtGbgKdPVRAmnGxHpVE"
DEFAULT_TIMEOUT = 120


def normalize_secret(raw_value: str | None) -> str:
	value = (raw_value or "").strip().replace("\ufeff", "")
	if not value:
		return ""
	value = value.removeprefix("Bearer ").removeprefix("bearer ").strip()
	if value.startswith("{"):
		try:
			payload = json.loads(value)
		except json.JSONDecodeError:
			payload = None
		if isinstance(payload, dict):
			for key in ("refresh_token", "offline_token", "access_token", "token"):
				candidate = payload.get(key)
				if isinstance(candidate, str) and candidate.strip():
					return normalize_secret(candidate)
	for separator in ("=", ":"):
		for key in ("refresh_token", "offline_token", "access_token", "token"):
			prefix = f"{key}{separator}"
			if value.lower().startswith(prefix):
				return normalize_secret(value[len(prefix):])
	if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
		return value[1:-1].strip()
	return value


def load_credentials(file_path: Path) -> dict[str, str]:
	if not file_path.exists():
		return {}
	credentials: dict[str, str] = {}
	with file_path.open("r", encoding="utf-8") as file_handle:
		for raw_line in file_handle:
			line = raw_line.strip()
			if not line or line.startswith("#") or "=" not in line:
				continue
			key, value = line.split("=", 1)
			credentials[key.strip()] = normalize_secret(value)
	return credentials


def build_asf_session(credentials_path: Path, session_factory: Callable[[], Session] = requests.Session) -> Session:
	session = session_factory()
	credentials = load_credentials(credentials_path)
	token = normalize_secret(os.getenv("EARTHDATA_TOKEN") or credentials.get("EARTHDATA_TOKEN"))
	username = normalize_secret(os.getenv("EARTHDATA_USERNAME") or credentials.get("EARTHDATA_USERNAME"))
	password = normalize_secret(os.getenv("EARTHDATA_PASSWORD") or credentials.get("EARTHDATA_PASSWORD"))
	if token:
		session.headers.update({"Authorization": f"Bearer {token}"})
	elif username and password:
		session.auth = (username, password)
	return session


def get_biomass_access_token(
	credentials_path: Path,
	request_post: Callable[..., Any] = requests.post,
	timeout: int = DEFAULT_TIMEOUT,
) -> str:
	credentials = load_credentials(credentials_path)
	access_token = normalize_secret(os.getenv("ESA_MAAP_ACCESS_TOKEN") or credentials.get("ACCESS_TOKEN"))
	if access_token:
		return access_token
	offline_token = normalize_secret(os.getenv("ESA_MAAP_OFFLINE_TOKEN") or credentials.get("OFFLINE_TOKEN"))
	if not offline_token:
		raise ValueError(
			"Missing OFFLINE_TOKEN. Set ESA_MAAP_OFFLINE_TOKEN or provide OFFLINE_TOKEN in credentials.txt."
		)
	client_id = normalize_secret(os.getenv("ESA_MAAP_CLIENT_ID") or credentials.get("CLIENT_ID")) or ESA_MAAP_DEFAULT_CLIENT_ID
	client_secret = normalize_secret(os.getenv("ESA_MAAP_CLIENT_SECRET") or credentials.get("CLIENT_SECRET")) or ESA_MAAP_DEFAULT_CLIENT_SECRET
	response = request_post(
		ESA_MAAP_TOKEN_URL,
		data={
			"client_id": client_id,
			"client_secret": client_secret,
			"grant_type": "refresh_token",
			"refresh_token": offline_token,
			"scope": "offline_access openid",
		},
		timeout=timeout,
	)
	if response.status_code >= 400:
		message = response.text.strip()
		if len(message) > 300:
			message = message[:300] + "..."
		raise RuntimeError(
			f"Failed to obtain ESA MAAP access token. HTTP {response.status_code}. Response: {message}"
		)
	payload = response.json()
	token = normalize_secret(payload.get("access_token"))
	if not token:
		raise RuntimeError("ESA MAAP IAM response did not include access_token.")
	return token