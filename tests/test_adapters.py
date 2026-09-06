import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from mine_video.adapters import CaptureGuard, Minecraft, OBS, copy_recording
from mine_video.config import Settings


class AdapterTests(unittest.TestCase):
    def test_record_start_waits_for_encoder_readiness(self):
        obs = OBS(Settings())
        responses = iter([
            {"outputActive": False, "outputDuration": 0},
            {"outputActive": True, "outputDuration": 100},
        ])
        obs.call = lambda kind: {} if kind == "StartRecord" else next(responses)
        with patch("mine_video.runtime.time.sleep"):
            self.assertEqual(obs.start(), .1)

    def test_scoreboard_response_is_checked_instead_of_assuming_success(self):
        mc = Minecraft(Settings())
        mc.command = lambda _: "#tick has 123 [mv]"
        self.assertEqual(mc.score("tick"), 123)
        mc.command = lambda _: "Can't get value of mv for #tick; none is set"
        with self.assertRaises(RuntimeError):
            mc.score("tick")

    def test_guard_rejects_stale_render_menu_and_wrong_camera_position(self):
        guard = CaptureGuard(Settings())
        valid = {"protocol": 1, "player": "CameraBot", "server": "localhost:25565",
                 "renderAgeMs": 10, "world": "minevideo:studio", "hudHidden": True,
                 "screen": None, "position": [24, 101, 30]}
        guard.status = lambda: valid
        guard.ready("24 101 30 facing 0 84 0")
        for change in ({"renderAgeMs": 3000}, {"screen": "TitleScreen"}, {"position": [0, 0, 0]},
                       {"world": "minecraft:overworld"}, {"player": "OtherPlayer"}):
            guard.status = lambda change=change: {**valid, **change}
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                guard.ready("24 101 30 facing 0 84 0")

    def test_program_composite_is_checked_and_black_capture_rejected(self):
        image = Image.new("RGB", (320, 180), "black")
        obs = OBS(Settings())
        def call(kind, data=None):
            if kind == "GetCurrentProgramScene":
                return {"currentProgramSceneName": "MineVideo"}
            self.assertEqual(kind, "GetSourceScreenshot")
            self.assertEqual(data["sourceName"], "MineVideo")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return {"imageData": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()}
        obs.call = call
        with self.assertRaisesRegex(RuntimeError, "black or blank"):
            obs.sample()
        ImageDraw.Draw(image).rectangle((10, 10, 200, 150), fill="green")
        self.assertGreater(obs.sample()["deviation"], 2)

    def test_obs_paths_must_be_inside_the_configured_recording_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.mkv"
            outside.write_bytes(b"recording")
            obs_root, target = root / "obs", root / "job"
            obs_root.mkdir()
            target.mkdir()
            with self.assertRaisesRegex(RuntimeError, "outside recording_root"):
                copy_recording(outside, obs_root, target)
            source = obs_root / "recording.mkv"
            source.write_bytes(b"real file transfer fixture")
            with patch("mine_video.runtime.time.sleep"):
                copied = copy_recording(source, obs_root, target)
            self.assertEqual(copied.read_bytes(), source.read_bytes())

    def test_datapack_deployment_waits_for_chunks_before_prepare(self):
        mc = Minecraft(Settings())
        calls = []
        scores = {"nonce": 42, "loaded": 1, "ready": 1}
        mc.command = lambda command: calls.append(command) or "ok"
        mc.score = lambda key: scores[key]
        mc.deploy(42, lambda: None)
        load_index = next(i for i, cmd in enumerate(calls) if "if loaded" in cmd)
        self.assertLess(load_index, calls.index("function minevideo:prepare"))
        self.assertEqual(calls[load_index].count("if loaded"), 16)
