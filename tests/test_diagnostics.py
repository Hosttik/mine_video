import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mine_video.config import Settings
from mine_video.diagnostics import bundle, collect


class FakeMinecraft:
    def status(self):
        return {"nonce": 1, "run": 2, "tick": 300, "result": 0}


class FakeGuard:
    def status(self):
        return {"protocol": 1, "renderAgeMs": 12}


class FakeOBS:
    def call(self, kind):
        return {"kind": kind}

    def screenshot(self, path):
        path.write_bytes(b"png fixture")
        return {"file": str(path), "sha256": "abc"}


class DiagnosticsTests(unittest.TestCase):
    def test_bundle_contains_runtime_state_but_not_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Settings(data_dir=root / "data", recording_root=root / "recordings",
                              datapack_dir=root / "packs")
            config.api_token = "api-secret"
            config.rcon_password = "rcon-secret"
            config.obs_password = "obs-secret"
            config.guard_token = "guard-secret"
            job_dir = root / "job"
            job_dir.mkdir()
            (job_dir / "request.json").write_text('{"seed":42}', encoding="utf-8")

            def safe_error(error):
                value = str(error)
                for secret in (config.api_token, config.rcon_password, config.obs_password, config.guard_token):
                    value = value.replace(secret, "[redacted]")
                return value

            collect(config, FakeMinecraft(), FakeOBS(), FakeGuard(), job_dir, "failure", safe_error,
                    RuntimeError("boom obs-secret"))
            archive = bundle(job_dir)
            with zipfile.ZipFile(archive) as zipped:
                names = set(zipped.namelist())
                self.assertIn("diagnostics/failure.json", names)
                self.assertIn("diagnostics/failure-obs.png", names)
                payload = b"".join(zipped.read(name) for name in names)
            self.assertNotIn(b"api-secret", payload)
            self.assertNotIn(b"rcon-secret", payload)
            self.assertNotIn(b"obs-secret", payload)
            self.assertNotIn(b"guard-secret", payload)
            report = json.loads((job_dir / "diagnostics" / "failure.json").read_text())
            self.assertEqual(report["error"], "boom [redacted]")
