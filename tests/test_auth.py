from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scheduler.auth import build_asf_session, get_biomass_access_token, load_credentials, normalize_secret


class _FakeTokenResponse:
	def __init__(self, status_code: int, payload: dict[str, str], text: str = ""):
		self.status_code = status_code
		self._payload = payload
		self.text = text

	def json(self):
		return self._payload


class AuthTests(unittest.TestCase):
	def setUp(self) -> None:
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		self.credentials_path = Path(self.temp_dir.name) / "credentials.txt"

	def test_load_credentials_reads_key_value_pairs(self) -> None:
		self.credentials_path.write_text(
			"EARTHDATA_USERNAME=user\nEARTHDATA_PASSWORD=secret\nOFFLINE_TOKEN='offline-token'\n",
			encoding="utf-8",
		)
		credentials = load_credentials(self.credentials_path)
		self.assertEqual(credentials["EARTHDATA_USERNAME"], "user")
		self.assertEqual(credentials["OFFLINE_TOKEN"], "offline-token")

	def test_build_asf_session_prefers_bearer_token(self) -> None:
		self.credentials_path.write_text("EARTHDATA_TOKEN=bearer test-token\n", encoding="utf-8")
		session = build_asf_session(self.credentials_path)
		self.assertEqual(session.headers["Authorization"], "Bearer test-token")

	def test_get_biomass_access_token_uses_existing_access_token(self) -> None:
		with patch.dict(os.environ, {"ESA_MAAP_ACCESS_TOKEN": "Bearer direct-token"}, clear=False):
			self.assertEqual(get_biomass_access_token(self.credentials_path), "direct-token")

	def test_get_biomass_access_token_uses_offline_token_exchange(self) -> None:
		self.credentials_path.write_text("OFFLINE_TOKEN=refresh-token\n", encoding="utf-8")

		def _fake_post(url, data=None, timeout=None):
			self.assertEqual(data["refresh_token"], "refresh-token")
			return _FakeTokenResponse(200, {"access_token": "access-from-iam"})

		self.assertEqual(get_biomass_access_token(self.credentials_path, request_post=_fake_post), "access-from-iam")

	def test_normalize_secret_handles_json_and_prefixes(self) -> None:
		self.assertEqual(normalize_secret('{"access_token": "abc"}'), "abc")
		self.assertEqual(normalize_secret("refresh_token=xyz"), "xyz")


if __name__ == "__main__":
	unittest.main()