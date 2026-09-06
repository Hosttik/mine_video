import itertools
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mine_video.config import Settings
from mine_video.models import JobSpec
from mine_video.worker import Worker


class FakeMinecraft:
    def __init__(self):
        self.nonce, self.calls, self.cleanups = 0, 0, 0
        self.on_status = lambda: None

    def connect(self): return True
    def online(self): return True
    def deploy(self, nonce, _): self.nonce = nonce
    def start(self): pass
    def close(self): pass
    def cleanup(self): self.cleanups += 1
    def status(self):
        self.on_status()
        self.calls += 1
        return {"nonce": self.nonce, "run": 2 if self.calls >= 3 else 1,
                "tick": self.calls * 20, "result": 0}


class FakeOBS:
    def __init__(self, source):
        self.source, self.active, self.stops, self.seconds = source, False, 0, 0
        self.frozen = False
        self.frames = 0

    def connect(self): return True
    def close(self): pass
    def preflight(self):
        if self.active:
            raise RuntimeError("OBS is already recording")
        return self.sample()
    def call(self, kind):
        if kind == "GetProfileList":
            return {"currentProfileName": "MineVideo"}
        if kind == "GetRecordStatus":
            return {"outputActive": self.active}
        raise AssertionError(kind)
    def sample(self):
        self.frames += 1
        return {"sha256": str(1 if self.frozen else self.frames)}
    def start(self): self.active = True
    def record_seconds(self):
        self.seconds += 1
        return self.seconds
    def stop(self):
        self.stops += 1
        self.active = False
        return self.source


class FakeGuard:
    def ready(self, _): return {"protocol": 1}


def fake_renderer(_config, _spec, _plan, _raw, directory, _timing, check_cancel):
    check_cancel()
    (directory / "short.mp4").write_bytes(b"verified by separate real FFmpeg test")
    return {"short": "short.mp4"}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = Settings(data_dir=root / "data", datapack_dir=root / "packs", recording_root=root / "obs")
        self.config.datapack_dir.mkdir()
        (self.config.datapack_dir / "minevideo.zip").write_bytes(b"bootstrap pack")
        self.config.recording_root.mkdir()
        source = self.config.recording_root / "recording.mkv"
        source.write_bytes(b"fake transport fixture")
        self.mc, self.obs = FakeMinecraft(), FakeOBS(source)
        self.worker = Worker(self.config, minecraft=self.mc, obs=self.obs, guard=FakeGuard(), renderer=fake_renderer)
        self.job = self.worker.store.submit(JobSpec())
        self.worker.store.claim()
        self.addCleanup(patch.stopall)
        patch("mine_video.worker.time.monotonic", side_effect=itertools.count(0, .2)).start()
        patch("mine_video.worker.time.sleep").start()
        def copy(src, _root, directory, check):
            check()
            target = directory / "raw.mkv"
            shutil.copyfile(src, target)
            return target
        patch("mine_video.worker.copy_recording", side_effect=copy).start()

    def test_success_stops_obs_and_publishes_checksums(self):
        self.worker.execute(self.job)
        result = self.worker.store.get(self.job["id"])
        self.assertEqual(result["state"], "succeeded", result["error"])
        self.assertEqual(self.obs.stops, 1)
        self.assertEqual(self.mc.cleanups, 1)
        self.assertFalse(self.worker.marker.exists())
        manifest = json.loads((self.config.data_dir / "jobs" / self.job["id"] / "manifest.json").read_text())
        self.assertEqual(len(manifest["short"]["sha256"]), 64)

    def test_existing_recording_is_never_stopped(self):
        self.obs.active = True
        self.worker.execute(self.job)
        self.assertEqual(self.obs.stops, 0)
        self.assertTrue(self.obs.active)
        self.assertEqual(self.worker.store.get(self.job["id"])["state"], "failed")

    def test_cancel_during_capture_stops_our_recording(self):
        self.mc.on_status = lambda: self.worker.store.cancel(self.job["id"])
        self.worker.execute(self.job)
        self.assertEqual(self.obs.stops, 1)
        self.assertEqual(self.worker.store.get(self.job["id"])["state"], "cancelled")

    def test_game_failure_stops_obs_and_does_not_publish(self):
        def fail(): raise RuntimeError("RCON connection dropped")
        self.mc.on_status = fail
        self.worker.execute(self.job)
        self.assertEqual(self.obs.stops, 1)
        result = self.worker.store.get(self.job["id"])
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["artifacts"])

    def test_frozen_source_is_rejected_after_stopping_recording(self):
        self.obs.frozen = True
        self.worker.execute(self.job)
        self.assertEqual(self.obs.stops, 1)
        self.assertIn("frozen", self.worker.store.get(self.job["id"])["error"])

    def test_recovery_keeps_marker_if_obs_is_unreachable(self):
        self.worker.marker.write_text(json.dumps({"job": self.job["id"], "profile": "MineVideo"}))
        self.obs.connect = lambda: (_ for _ in ()).throw(ConnectionError("unreachable"))
        with self.assertRaises(ConnectionError):
            self.worker.recover()
        self.assertTrue(self.worker.marker.exists())
