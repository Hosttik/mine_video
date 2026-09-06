import os
import platform
import sys
import time
import zipfile
from pathlib import Path

from .runtime import atomic_json


def _safe_capture(report, name, action, safe_error):
    try:
        report["checks"][name] = {"ok": True, "detail": action()}
    except Exception as error:
        report["checks"][name] = {"ok": False, "detail": safe_error(error)}


def _redact(value, secrets):
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
    return value


def collect(config, mc, obs, guard, job_dir: Path, stage: str, safe_error, error=None):
    """Capture best-effort machine state without ever including local secrets."""
    root = job_dir / "diagnostics"
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "stage": stage,
        "at": time.time(),
        "error": safe_error(error) if error is not None else None,
        "system": {
            "platform": platform.platform(),
            "python": sys.version,
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
        },
        "config": {
            "base_dir": str(config.base_dir),
            "data_dir": str(config.data_dir),
            "recording_root": str(config.recording_root),
            "datapack_dir": str(config.datapack_dir),
            "minecraft": {
                "host": config.minecraft.host,
                "port": config.minecraft.port,
                "player": config.minecraft.player,
                "server_address": config.minecraft.server_address,
                "server_command": config.minecraft.server_command,
                "client_command": config.minecraft.client_command,
            },
            "obs": {
                "host": config.obs.host,
                "port": config.obs.port,
                "profile": config.obs.profile,
                "scene": config.obs.scene,
                "source": config.obs.source,
                "command": config.obs.command,
            },
            "media": {
                "ffmpeg": config.media.ffmpeg,
                "ffprobe": config.media.ffprobe,
                "width": config.media.width,
                "height": config.media.height,
                "fps": config.media.fps,
            },
        },
        "checks": {},
    }

    _safe_capture(report, "capture_guard", guard.status, safe_error)
    _safe_capture(report, "minecraft", mc.status, safe_error)

    def obs_state():
        return {
            "profile": obs.call("GetProfileList"),
            "record": obs.call("GetRecordStatus"),
            "stream": obs.call("GetStreamStatus"),
            "program_scene": obs.call("GetCurrentProgramScene"),
        }

    _safe_capture(report, "obs", obs_state, safe_error)

    screenshot = root / f"{stage}-obs.png"
    _safe_capture(report, "obs_screenshot", lambda: obs.screenshot(screenshot), safe_error)

    secrets = (config.rcon_password, config.obs_password, config.guard_token, config.api_token)
    report = _redact(report, secrets)
    target = root / f"{stage}.json"
    atomic_json(target, report)
    return target


def bundle(job_dir: Path):
    """Create a portable, secret-free bundle suitable for attaching to a bug report."""
    target = job_dir / "diagnostics.zip"
    temp = target.with_suffix(".zip.tmp")
    candidates = [
        job_dir / "diagnostics",
        job_dir / "failure.json",
        job_dir / "plan.json",
        job_dir / "request.json",
        job_dir / "capture.json",
        job_dir / "server-start.log",
        job_dir / "client.log",
        job_dir / "obs.log",
        job_dir / "stopped-recording.json",
    ]
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
        for candidate in candidates:
            if candidate.is_dir():
                for path in sorted(candidate.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(job_dir))
            elif candidate.is_file():
                archive.write(candidate, candidate.relative_to(job_dir))
    temp.replace(target)
    return target
