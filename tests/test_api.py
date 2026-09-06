import importlib.util
import tempfile
import unittest
from pathlib import Path

from mine_video.config import Settings


@unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"), "API dependencies")
class APITests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from mine_video.api import create_app
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Settings(data_dir=Path(self.temp.name), api_token="a" * 32)
        self.client = TestClient(create_app(self.config))
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer " + self.config.api_token}

    def test_auth_validation_and_idempotency(self):
        self.assertEqual(self.client.post("/jobs", json={}).status_code, 401)
        headers = {**self.headers, "Idempotency-Key": "episode-1"}
        first = self.client.post("/jobs", headers=headers, json={})
        self.assertEqual(first.status_code, 202)
        second = self.client.post("/jobs", headers=headers, json={})
        self.assertEqual(first.json()["id"], second.json()["id"])
        conflict = self.client.post("/jobs", headers=headers, json={"seed": 99})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.client.post("/jobs", headers=self.headers, json={"commands": ["stop"]}).status_code, 422)

    def test_cancel_and_artifact_access_do_not_expose_local_files(self):
        job = self.client.post("/jobs", headers=self.headers, json={}).json()
        cancelled = self.client.post(f"/jobs/{job['id']}/cancel", headers=self.headers)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        result = self.client.get(f"/jobs/{job['id']}/artifacts/metadata", headers=self.headers)
        self.assertEqual(result.status_code, 404)
        self.assertFalse(self.client.get("/health").json()["worker_ready"])
