import hashlib
import json
import os
import shutil
import threading
import time

from . import media
from .adapters import CaptureGuard, Minecraft, OBS, copy_recording
from .diagnostics import bundle as bundle_diagnostics
from .diagnostics import collect as collect_diagnostics
from .models import JobSpec
from .runtime import Cancelled, atomic_json, run_process, start_process, wait_for
from .scenes import compile_plan, write_pack
from .store import Store


class Worker:
    def __init__(self, config, *, minecraft=None, obs=None, guard=None, renderer=media.render):
        self.config = config
        self.store = Store(config.database)
        self.mc = minecraft or Minecraft(config)
        self.obs = obs or OBS(config)
        self.guard = guard or CaptureGuard(config)
        self.renderer = renderer
        self.stop = threading.Event()
        self.marker = config.data_dir / "recording-owner.json"
        self.active_job = None
        self.last_heartbeat = 0
        self.gui_processes = []

    def check_cancel(self):
        self.heartbeat()
        if self.stop.is_set():
            raise InterruptedError("Worker shutdown requested")
        if self.active_job and self.store.get(self.active_job)["cancel_requested"]:
            raise Cancelled("Job cancelled")

    def heartbeat(self):
        if time.monotonic() - self.last_heartbeat >= 1:
            atomic_json(self.config.data_dir / "worker.json", {
                "pid": os.getpid(), "at": time.time(), "job": self.active_job,
            })
            self.last_heartbeat = time.monotonic()
        self.gui_processes = [p for p in self.gui_processes if p.poll() is None]

    def ensure_runtime(self, job_dir):
        cfg = self.config
        try:
            self.mc.connect()
        except Exception:
            if not cfg.minecraft.server_command:
                raise
            run_process(cfg.minecraft.server_command, cwd=cfg.base_dir, log=job_dir / "server-start.log",
                        timeout=90, check_cancel=self.check_cancel)
            wait_for(self.mc.connect, timeout=cfg.minecraft.startup_timeout, check_cancel=self.check_cancel)
        try:
            self.mc.online()
        except Exception:
            # A running guarded client can reconnect from its title/disconnect screen.
            try:
                self.guard.connect_game()
            except (OSError, RuntimeError):
                self.gui_processes.append(start_process(cfg.minecraft.client_command, cwd=cfg.base_dir,
                                                        log=job_dir / "client.log"))
            wait_for(self.mc.online, timeout=cfg.minecraft.startup_timeout, check_cancel=self.check_cancel)
        try:
            self.obs.connect()
        except Exception:
            self.gui_processes.append(start_process(cfg.obs.command, cwd=cfg.base_dir, log=job_dir / "obs.log"))
            wait_for(self.obs.connect, timeout=60, check_cancel=self.check_cancel)

    def stop_owned_recording(self):
        if not self.marker.exists():
            return None
        owner = json.loads(self.marker.read_text())
        if self.obs.call("GetProfileList")["currentProfileName"] != owner["profile"]:
            raise RuntimeError("OBS profile differs from interrupted recording owner; resolve recording manually")
        path = None
        if self.obs.call("GetRecordStatus")["outputActive"]:
            path = self.obs.stop()
            atomic_json(self.config.data_dir / "jobs" / owner["job"] / "stopped-recording.json", {"path": str(path)})
        self.marker.unlink()
        return path

    def recover(self):
        if self.marker.exists():
            # Never discard the ownership marker when OBS is unreachable.
            self.obs.connect()
            self.stop_owned_recording()
        previous = self.store.recover()
        if previous:
            try:
                self.mc.connect()
                self.mc.cleanup()
            except Exception as error:
                atomic_json(self.config.data_dir / "recovery-warning.json", {"error": self.safe_error(error)})

    def safe_error(self, error):
        value = f"{type(error).__name__}: {error}"
        for secret in (self.config.rcon_password, self.config.obs_password,
                       self.config.guard_token, self.config.api_token):
            if secret:
                value = value.replace(secret, "[redacted]")
        return value[:1500]

    def write_diagnostics(self, job_dir, stage, error=None):
        return collect_diagnostics(
            self.config, self.mc, self.obs, self.guard, job_dir, stage, self.safe_error, error=error,
        )

    def execute(self, job):
        cfg, job_id = self.config, job["id"]
        self.active_job = job_id
        job_dir = cfg.data_dir / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        spec = JobSpec.model_validate(job["spec"])
        plan = compile_plan(spec, int(job_id[:8], 16) & 0x7FFFFFFF)
        (job_dir / "plan.json").write_text(plan.json(), encoding="utf-8")
        atomic_json(job_dir / "request.json", spec.model_dump())
        prepared = False
        try:
            self.check_cancel()
            for directory in (cfg.data_dir, cfg.recording_root, cfg.datapack_dir):
                directory.mkdir(parents=True, exist_ok=True)
                if shutil.disk_usage(directory).free < cfg.media.minimum_free_gb * 1_000_000_000:
                    raise RuntimeError(f"Insufficient free disk space in {directory.name}")
            if not (cfg.datapack_dir / "minevideo.zip").exists():
                raise RuntimeError("Studio datapack is missing; run init before starting Paper")
            self.ensure_runtime(job_dir)
            write_pack(job_dir / "datapack.zip", plan, cfg.minecraft.player)
            write_pack(cfg.datapack_dir / "minevideo.zip", plan, cfg.minecraft.player)
            prepared = True  # Even a partially executed prepare must be cleaned up.
            self.mc.deploy(plan.nonce, self.check_cancel)
            wait_for(lambda: self.guard.ready(plan.camera), timeout=45, check_cancel=self.check_cancel)
            first_frame = self.obs.preflight()
            self.check_cancel()
            # Durable intent before StartRecord covers a lost websocket response or worker crash.
            atomic_json(self.marker, {"job": job_id, "profile": cfg.obs.profile, "at": time.time()})
            self.obs.start()
            self.store.transition(job_id, "recording", "Client rendered the studio; OBS recording started")
            # Real pre-roll lets the encoder and exposure settle. It is trimmed from the export.
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                self.check_cancel()
                time.sleep(.1)
            offset = self.obs.record_seconds()
            self.mc.start()
            samples, frames = [[0, 0.0]], [first_frame]
            limit = time.monotonic() + plan.duration_ticks / 20 * 3 + 30
            last_tick, last_progress, last_frame = 0, time.monotonic(), time.monotonic()
            while True:
                self.check_cancel()
                state = self.mc.status()
                now = time.monotonic()
                if state["nonce"] != plan.nonce or state["run"] not in (1, 2):
                    raise RuntimeError("Scene replaced, reset, or stopped unexpectedly")
                if state["tick"] < last_tick:
                    raise RuntimeError("Server timeline moved backwards")
                if state["tick"] > last_tick:
                    last_progress = now
                last_tick = state["tick"]
                if now - last_progress > 15 or now > limit:
                    raise TimeoutError("Minecraft stopped advancing the scene")
                self.mc.online()
                self.guard.ready(plan.camera)
                seconds = self.obs.record_seconds() - offset
                if seconds < samples[-1][1]:
                    raise RuntimeError("OBS recording clock moved backwards")
                samples.append([state["tick"], seconds])
                if now - last_frame >= 1.5 or state["run"] == 2:
                    frame = self.obs.sample()
                    frame["at"] = round(seconds, 3)
                    frames.append(frame)
                    last_frame = now
                if state["run"] == 2:
                    break
                time.sleep(.5)
            duration = self.obs.record_seconds() - offset
            raw_source = self.stop_owned_recording()
            if raw_source is None:
                raise RuntimeError("OBS returned no recording path")
            timing = {"offset": offset, "duration": duration, "samples": samples, "result": state["result"]}
            atomic_json(job_dir / "capture.json", {"timing": timing, "frames": frames,
                        "observed_tps": round(state["tick"] / max(.1, duration), 2),
                        "source": "Minecraft client via OBS", "guard_protocol": 1})
            self.mc.cleanup()
            prepared = False
            if len({f["sha256"] for f in frames}) < 2:
                raise RuntimeError("OBS source stayed frozen throughout the scene")
            raw = copy_recording(raw_source, cfg.recording_root, job_dir, self.check_cancel)
            self.store.transition(job_id, "rendering", "Capture complete; exporting video and thumbnail")
            artifacts = self.renderer(cfg, spec, plan, raw, job_dir, timing, self.check_cancel)
            self.check_cancel()
            try:
                self.write_diagnostics(job_dir, "success")
                artifacts["diagnostics"] = bundle_diagnostics(job_dir).name
            except Exception as diagnostic_error:
                atomic_json(job_dir / "diagnostics-warning.json", {"error": self.safe_error(diagnostic_error)})
            manifest = {}
            for name, filename in artifacts.items():
                path = job_dir / filename
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                        self.check_cancel()
                        digest.update(chunk)
                manifest[name] = {"file": filename, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}
            atomic_json(job_dir / "manifest.json", manifest)
            artifacts["manifest"] = "manifest.json"
            self.store.transition(job_id, "succeeded", "Video exports verified", artifacts=artifacts)
        except BaseException as error:
            cleanup_errors = []
            diagnostic_errors = []
            try:
                self.write_diagnostics(job_dir, "failure", error)
            except Exception as diagnostic_error:
                diagnostic_errors.append(self.safe_error(diagnostic_error))
            try:
                if self.marker.exists():
                    self.stop_owned_recording()
            except Exception as cleanup_error:
                cleanup_errors.append(self.safe_error(cleanup_error))
            if prepared:
                try:
                    self.mc.cleanup()
                except Exception as cleanup_error:
                    cleanup_errors.append(self.safe_error(cleanup_error))
            failure = {
                "error": self.safe_error(error),
                "cleanup_errors": cleanup_errors,
                "diagnostic_errors": diagnostic_errors,
            }
            atomic_json(job_dir / "failure.json", failure)
            diagnostic_artifacts = {}
            try:
                diagnostic_artifacts["diagnostics"] = bundle_diagnostics(job_dir).name
            except Exception as diagnostic_error:
                failure["diagnostic_errors"].append(self.safe_error(diagnostic_error))
                atomic_json(job_dir / "failure.json", failure)
            state = ("cancelled" if isinstance(error, Cancelled) else "interrupted"
                     if isinstance(error, (KeyboardInterrupt, InterruptedError, SystemExit)) else "failed")
            self.store.transition(job_id, state, failure["error"], artifacts=diagnostic_artifacts)
            if cleanup_errors or isinstance(error, (KeyboardInterrupt, SystemExit)):
                # An uncertain recorder is not safe to reuse for the next queued job.
                raise RuntimeError("Worker stopped after cleanup failure; see failure.json") from error
        finally:
            self.active_job = None

    def run(self, once=False):
        import portalocker
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(self.config.data_dir / "worker.lock"), timeout=0):
            self.recover()
            try:
                while not self.stop.is_set():
                    self.heartbeat()
                    job = self.store.claim()
                    if job:
                        self.execute(job)
                    if once:
                        break
                    if not job:
                        time.sleep(.5)
            finally:
                self.mc.close()
                self.obs.close()
                (self.config.data_dir / "worker.json").unlink(missing_ok=True)
