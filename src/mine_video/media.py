import bisect
import json
import subprocess
import textwrap
from pathlib import Path

from .runtime import atomic_json, run_process
from .scenes import outcome


def probe(path: Path, binary="ffprobe"):
    result = subprocess.run([
        binary, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ], capture_output=True, text=True, timeout=30, check=True)
    data = json.loads(result.stdout)
    videos = [s for s in data["streams"] if s["codec_type"] == "video"]
    if not videos or float(data["format"].get("duration", 0)) <= 0:
        raise RuntimeError("Recording does not contain a usable video stream")
    return data


def timestamp(seconds):
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{cs:02}"


def caption_text(text):
    # ASS override sequences are executable styling: user text must never become one.
    safe = text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
    return "\\N".join(textwrap.wrap(safe, width=42, break_long_words=True))


def tick_time(tick, samples):
    ticks = [s[0] for s in samples]
    index = bisect.bisect_left(ticks, tick)
    if index == 0:
        return samples[0][1]
    if index == len(samples):
        return samples[-1][1]
    a, b = samples[index - 1], samples[index]
    if a[0] == b[0]:
        return b[1]
    return a[1] + (tick - a[0]) / (b[0] - a[0]) * (b[1] - a[1])


def write_captions(path, width, height, captions, portrait=False):
    size = round(width * (0.055 if portrait else 0.031))
    margin = round(height * (0.15 if portrait else 0.07))
    align = 8 if portrait else 2
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,DejaVu Sans,{size},&H00FFFFFF,&H00FFFFFF,&H00141414,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,{align},{round(width * .07)},{round(width * .07)},{margin},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = [f"Dialogue: 0,{timestamp(start)},{timestamp(end)},Main,,0,0,0,,{caption_text(text)}"
            for start, end, text in captions if end - start > 0.1]
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


def render(config, spec, plan, raw: Path, job_dir: Path, timing: dict, check_cancel=lambda: None):
    cfg = config.media
    info = probe(raw, cfg.ffprobe)
    duration, offset = timing["duration"], timing["offset"]
    if duration < 3 or float(info["format"]["duration"]) < offset + duration - 0.5:
        raise RuntimeError("Recording is too short or was truncated")
    if not any(s["codec_type"] == "audio" for s in info["streams"]):
        raise RuntimeError("OBS recording has no audio stream; configure game audio before recording")
    captions = [(tick_time(c.tick, timing["samples"]), tick_time(c.end_tick, timing["samples"]), c.text)
                for c in plan.captions if c.tick < timing["samples"][-1][0]]
    result_text = outcome(spec, timing["result"])
    if spec.template == "mob_arena":
        captions.append((max(0, duration - 2.5), duration, result_text))
    artifacts = {"raw": raw.name, "plan": "plan.json", "capture": "capture.json"}
    rendered = {}
    for mode in spec.formats:
        check_cancel()
        portrait = mode == "short"
        width, height = (cfg.height, cfg.width) if portrait else (cfg.width, cfg.height)
        ass = f"captions-{mode}.ass"
        write_captions(job_dir / ass, width, height, captions, portrait)
        if portrait:
            # Full gameplay stays visible; never crop the fighter at the edge of a wide shot.
            filters = (
                f"[0:v]split[bg][fg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},gblur=sigma=28[background];"
                f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[foreground];"
                f"[background][foreground]overlay=(W-w)/2:(H-h)/2,setsar=1,ass={ass}[video]"
            )
        else:
            filters = (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,ass={ass}[video]")
        filename, temp = f"{mode}.mp4", f"{mode}.partial.mp4"
        args = [
            cfg.ffmpeg, "-hide_banner", "-y", "-nostdin", "-filter_complex_threads", "1",
            "-ss", f"{offset:.3f}", "-i", str(raw.resolve()), "-t", f"{duration:.3f}",
            "-filter_complex", filters, "-map", "[video]", "-map", "0:a:0",
            "-c:v", "libx264", "-threads", "2", "-preset", cfg.preset, "-crf", str(cfg.crf),
            "-pix_fmt", "yuv420p", "-r", str(cfg.fps), "-c:a", "aac", "-b:a", "192k",
            "-af", "aresample=async=1:first_pts=0", "-movflags", "+faststart", temp,
        ]
        run_process(args, cwd=job_dir, log=job_dir / f"ffmpeg-{mode}.log",
                    timeout=cfg.render_timeout, check_cancel=check_cancel)
        output = probe(job_dir / temp, cfg.ffprobe)
        stream = next(s for s in output["streams"] if s["codec_type"] == "video")
        if (stream["width"], stream["height"]) != (width, height):
            raise RuntimeError("Encoder produced incorrect output dimensions")
        if abs(float(output["format"]["duration"]) - duration) > .75:
            raise RuntimeError("Encoder produced an incomplete video")
        (job_dir / temp).replace(job_dir / filename)
        artifacts[mode] = filename
        rendered[mode] = {"width": width, "height": height, "duration": float(output["format"]["duration"])}
    # Pick an action/reveal frame from the actual recording, with no generated imagery.
    moment = min(duration - 0.2, max(2, duration * (0.85 if spec.template == "tower_build" else 0.4)))
    run_process([
        cfg.ffmpeg, "-hide_banner", "-y", "-nostdin", "-ss", str(offset + moment), "-i", str(raw.resolve()),
        "-frames:v", "1", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
        "-q:v", "2", "-update", "1", "thumbnail.jpg",
    ], cwd=job_dir, log=job_dir / "ffmpeg-thumbnail.log", timeout=60, check_cancel=check_cancel)
    if not (job_dir / "thumbnail.jpg").is_file():
        raise RuntimeError("Thumbnail was not created")
    artifacts["thumbnail"] = "thumbnail.jpg"
    metadata = {
        "title": plan.title, "description": f"{plan.title}\n{result_text}\nRecorded in Minecraft Java {info_version()}.",
        "tags": ["minecraft", spec.template.replace("_", " "), "gameplay"],
        "language": spec.language, "outcome": result_text, "seed": spec.seed,
        "capture_type": "real_minecraft_client_obs", "formats": rendered,
        "short_layout": "full gameplay on blurred background" if "short" in spec.formats else None,
        "published_to_youtube": False,
    }
    atomic_json(job_dir / "metadata.json", metadata)
    artifacts["metadata"] = "metadata.json"
    return artifacts


def info_version():
    from .scenes import MC_VERSION
    return MC_VERSION
