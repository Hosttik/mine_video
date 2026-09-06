import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mine_video.config import Settings
from mine_video.media import caption_text, probe, render, tick_time
from mine_video.models import JobSpec
from mine_video.scenes import compile_plan


class TimingTests(unittest.TestCase):
    def test_lagged_server_captions_follow_recorded_clock(self):
        self.assertEqual(tick_time(40, [[0, 0], [20, 1], [60, 5]]), 3)
        self.assertEqual(tick_time(200, [[0, 0], [20, 1], [60, 5]]), 5)

    def test_subtitle_text_cannot_inject_ass_overrides(self):
        text = caption_text(r"{\pos(0,0)} Real title")
        self.assertNotIn("{", text)
        self.assertNotIn(r"\pos", text)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class RealFFmpegTests(unittest.TestCase):
    def test_actual_encoder_exports_both_formats_audio_thumbnail_and_metadata(self):
        with tempfile.TemporaryDirectory(prefix="encoder's test ") as directory:
            root = Path(directory)
            raw = root / "synthetic_encoder_fixture.mkv"
            # Test-only video pattern. Production Worker has no synthetic recorder or fallback.
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                "testsrc2=size=320x180:rate=24:duration=5", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=5", "-c:v", "libx264", "-threads", "2", "-c:a", "aac", str(raw),
            ], check=True, timeout=30)
            config = Settings(media={"width": 640, "height": 360, "fps": 24, "preset": "ultrafast"})
            spec = JobSpec(title="Голем: проверка {текста}", language="ru")
            plan = compile_plan(spec, 1)
            artifacts = render(config, spec, plan, raw, root,
                               {"offset": .5, "duration": 4, "samples": [[0, 0], [80, 4]], "result": 0})
            for key, dimensions in (("landscape", (640, 360)), ("short", (360, 640))):
                result = probe(root / artifacts[key])
                video = next(s for s in result["streams"] if s["codec_type"] == "video")
                self.assertEqual((video["width"], video["height"]), dimensions)
                self.assertEqual(video["codec_name"], "h264")
                self.assertTrue(any(s["codec_type"] == "audio" for s in result["streams"]))
                self.assertAlmostEqual(float(result["format"]["duration"]), 4, delta=.2)
            self.assertGreater((root / artifacts["thumbnail"]).stat().st_size, 1000)
            metadata = json.loads((root / artifacts["metadata"]).read_text())
            self.assertFalse(metadata["published_to_youtube"])
            self.assertIn("победитель не определён", metadata["outcome"])
