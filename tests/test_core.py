import concurrent.futures
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from pydantic import ValidationError

from mine_video.bootstrap import initialize
from mine_video.config import load
from mine_video.models import JobSpec
from mine_video.scenes import compile_plan, outcome, pack_files, write_pack
from mine_video.store import Conflict, Store


class SceneTests(unittest.TestCase):
    def test_invalid_commands_cannot_enter_public_spec(self):
        for values in ({"commands": ["op someone"]}, {"mob_count": 5000}, {"duration_seconds": -1},
                       {"seed": True}, {"formats": ["short", "short"]}, {"template": "../../secret"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                JobSpec.model_validate(values)
        with self.assertRaises(ValueError):
            pack_files(compile_plan(JobSpec(), 1), "@a\nstop")

    def test_templates_generate_resolvable_functions_and_bounded_fills(self):
        for template in ("mob_arena", "tnt_chain", "tower_build"):
            for duration in (15, 40, 180):
                plan = compile_plan(JobSpec(template=template, duration_seconds=duration), 123)
                files = pack_files(plan, "CameraBot")
                self.assertIn("data/minecraft/tags/function/tick.json", files)
                for name, content in files.items():
                    if not name.endswith("mcfunction"):
                        json.loads(content)
                        continue
                    for target in re.findall(r"function minevideo:([a-z_0-9]+)", content):
                        self.assertIn(f"data/minevideo/function/{target}.mcfunction", files)
                    for line in content.splitlines():
                        if line.startswith("fill "):
                            xyz = list(map(int, line.split()[1:7]))
                            volume = 1
                            for i in range(3):
                                volume *= abs(xyz[i] - xyz[i + 3]) + 1
                            self.assertLessEqual(volume, 32768, line)
                self.assertTrue(all(t < plan.duration_ticks for t in plan.events))

    def test_seed_reproduces_scene_configuration_and_zip(self):
        plan = compile_plan(JobSpec(seed=912), 123)
        self.assertEqual(plan, compile_plan(JobSpec(seed=912), 123))
        self.assertNotEqual(plan.prepare, compile_plan(JobSpec(seed=913), 123).prepare)
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a.zip", Path(directory) / "b.zip"
            write_pack(a, plan, "CameraBot")
            write_pack(b, plan, "CameraBot")
            self.assertEqual(a.read_bytes(), b.read_bytes())
            with zipfile.ZipFile(a) as archive:
                self.assertIsNone(archive.testzip())

    def test_timeout_never_claims_a_winner(self):
        self.assertEqual(outcome(JobSpec(), 0), "Time limit — no winner")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "jobs.sqlite3")

    def test_concurrent_claims_are_unique(self):
        wanted = {self.store.submit(JobSpec(seed=i))["id"] for i in range(30)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            jobs = list(pool.map(lambda _: self.store.claim(), range(36)))
        ids = [j["id"] for j in jobs if j]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), wanted)

    def test_idempotency_is_atomic_and_payload_bound(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            jobs = list(pool.map(lambda _: self.store.submit(JobSpec(), "episode-1"), range(10)))
        self.assertEqual(len({j["id"] for j in jobs}), 1)
        with self.assertRaises(Conflict):
            self.store.submit(JobSpec(seed=9), "episode-1")

    def test_cancellation_wins_success_race(self):
        job = self.store.submit(JobSpec())
        self.store.claim()
        for state in ("recording", "rendering"):
            self.store.transition(job["id"], state, state)
        self.store.cancel(job["id"])
        self.store.transition(job["id"], "succeeded", "done", artifacts={"short": "short.mp4"})
        result = self.store.get(job["id"])
        self.assertEqual(result["state"], "cancelled")
        self.assertFalse(result["artifacts"])

    def test_cancelled_queued_jobs_are_not_claimed_and_recovery_is_explicit(self):
        first = self.store.submit(JobSpec())
        self.store.cancel(first["id"])
        self.assertIsNone(self.store.claim())
        second = self.store.submit(JobSpec(seed=100))
        self.store.claim()
        self.assertEqual(self.store.recover(), [second["id"]])
        self.assertEqual(self.store.get(second["id"])["state"], "interrupted")
        self.assertIsNone(self.store.claim())


class BootstrapTests(unittest.TestCase):
    def test_init_preserves_secrets_and_resolves_paths_from_configuration(self):
        with tempfile.TemporaryDirectory(prefix="studio with spaces ") as directory:
            path = Path(directory) / "mine-video.toml"
            initialize(path, "CameraBot")
            config = load(path)
            self.assertEqual(config.recording_root, Path(directory) / "recordings")
            self.assertGreaterEqual(len(config.api_token), 32)
            self.assertNotIn(config.api_token, repr(config))
            self.assertNotIn(config.api_token, config.model_dump_json())
            original = (Path(directory) / ".env").read_bytes()
            with self.assertRaises(RuntimeError):
                initialize(path, "SomeoneElse")
            self.assertEqual(original, (Path(directory) / ".env").read_bytes())
            self.assertTrue((config.datapack_dir / "minevideo.zip").exists())
