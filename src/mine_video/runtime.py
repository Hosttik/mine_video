import json
import os
import subprocess
import time
from pathlib import Path


class Cancelled(RuntimeError):
    pass


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def run_process(args, *, cwd: Path, log: Path, timeout: float, check_cancel=lambda: None):
    """No shell. Drain directly to disk, bound runtime, and reap on every exit path."""
    with log.open("ab") as output:
        process = subprocess.Popen(args, cwd=cwd, stdout=output, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                check_cancel()
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{Path(args[0]).name} timed out; see {log.name}")
                time.sleep(0.15)
            if process.returncode:
                raise RuntimeError(f"{Path(args[0]).name} exited with {process.returncode}; see {log.name}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def start_process(args, *, cwd: Path, log: Path):
    if not args:
        raise RuntimeError("Startup command is empty: start the configured application manually")
    if os.name == "nt" and Path(args[0]).is_absolute():
        # OBS on Windows resolves some resources from its executable directory.
        cwd = Path(args[0]).parent
    # GUI launchers may hand off to another process. Readiness comes from probes, never the PID.
    with log.open("ab") as output:
        return subprocess.Popen(args, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                start_new_session=os.name != "nt")


def wait_for(probe, *, timeout, check_cancel=lambda: None, interval=0.5):
    deadline, last = time.monotonic() + timeout, "not ready"
    while time.monotonic() < deadline:
        check_cancel()
        try:
            return probe()
        except Cancelled:
            raise
        except Exception as error:
            last = str(error)
        time.sleep(interval)
    raise TimeoutError(f"Readiness timed out: {last}")
