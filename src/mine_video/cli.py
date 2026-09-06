import argparse
import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .config import load
from .models import JobSpec, TERMINAL
from .store import Store


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def parser():
    root = argparse.ArgumentParser(description="Minecraft → OBS → verified MP4 production queue")
    root.add_argument("--config", default="mine-video.toml")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create local configuration, secrets, and studio datapack")
    init.add_argument("--player", required=True, help="The licensed Minecraft account's actual in-game name")
    pack = commands.add_parser("client-pack", help="Package a Prism instance with the capture guard")
    pack.add_argument("--guard-jar", type=Path, required=True)
    for command in ("submit", "preview"):
        submit = commands.add_parser(command)
        submit.add_argument("--template", choices=["mob_arena", "tnt_chain", "tower_build"], default="mob_arena")
        submit.add_argument("--seed", type=int, default=42)
        submit.add_argument("--duration", type=int, default=40)
        submit.add_argument("--mobs", type=int, default=12)
        submit.add_argument("--language", choices=["en", "ru"], default="en")
        submit.add_argument("--format", choices=["both", "landscape", "short"], default="both")
        submit.add_argument("--title")
        if command == "submit":
            submit.add_argument("--count", type=int, default=1)
    for command in ("status", "cancel", "wait"):
        sub = commands.add_parser(command)
        sub.add_argument("job_id", nargs="?" if command == "status" else None)
        if command == "wait":
            sub.add_argument("--timeout", type=int, default=900)
    for command in ("worker", "agent"):
        worker = commands.add_parser(
            command,
            help="Run the local recording machine" if command == "agent" else None,
        )
        worker.add_argument("--once", action="store_true")
    smoke = commands.add_parser("smoke-test", help="Record and render one real TNT scene on this machine")
    smoke.add_argument("--seed", type=int, default=20260906)
    smoke.add_argument("--timeout", type=int, default=420)
    for command in ("serve", "start"):
        serve = commands.add_parser(command)
        serve.add_argument("--host", default="127.0.0.1")
        serve.add_argument("--port", type=int, default=8000)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--launch", action="store_true", help="Start configured server, client and OBS if needed")
    return root


def doctor(config, launch=False):
    from .adapters import CaptureGuard, Minecraft, OBS
    from .worker import Worker
    checks = []

    def check(name, action):
        try:
            value = action()
            checks.append({"name": name, "ok": True, "detail": value})
        except Exception as error:
            checks.append({"name": name, "ok": False, "detail": str(error)})

    for name, executable in (("ffmpeg", config.media.ffmpeg), ("ffprobe", config.media.ffprobe)):
        def binary(executable=executable):
            found = shutil.which(executable)
            if not found:
                raise RuntimeError(f"Executable not found: {executable}")
            return found
        check(name, binary)
    if launch:
        launcher = Worker(config)
        directory = config.data_dir / "doctor"
        directory.mkdir(parents=True, exist_ok=True)
        check("startup", lambda: launcher.ensure_runtime(directory))
        launcher.mc.close()
        launcher.obs.close()
    mc, obs, guard = Minecraft(config), OBS(config), CaptureGuard(config)
    try:
        check("paper", mc.connect)
        check("camera_account", mc.online)
        check("obs_websocket", obs.connect)
        if obs.client:
            check("obs_source", obs.preflight)
        check("capture_guard", guard.status)
    finally:
        mc.close()
        obs.close()
    output(checks)
    return 0 if all(c["ok"] for c in checks) else 1


def worker_alive(config):
    try:
        heartbeat = json.loads((config.data_dir / "worker.json").read_text(encoding="utf-8"))
        return time.time() - float(heartbeat["at"]) < 15
    except (OSError, ValueError, TypeError, KeyError):
        return False


def wait_for_job(store, job_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get(job_id)
        if not job:
            raise RuntimeError("Smoke-test job disappeared from the queue")
        if job["state"] in TERMINAL:
            return job
        time.sleep(.5)
    raise TimeoutError("Smoke-test timed out; the job remains available for diagnosis")


def smoke_test(config, seed, timeout):
    if not 15 <= timeout <= 3600:
        raise ValueError("Smoke-test timeout must be between 15 and 3600 seconds")
    if not 0 <= seed <= 2_147_483_647:
        raise ValueError("Seed must be between 0 and 2147483647")
    store = Store(config.database)
    active = [job for job in store.list(200) if job["state"] not in TERMINAL]
    if active:
        raise RuntimeError(
            f"Studio is not idle: job {active[-1]['id']} is {active[-1]['state']}. "
            "Finish or cancel active jobs before smoke-test."
        )
    spec = JobSpec(
        template="tnt_chain",
        seed=seed,
        duration_seconds=20,
        mob_count=2,
        formats=["landscape"],
        language="en",
        title="MineVideo real capture smoke test",
    )
    job = store.submit(spec)
    job_id = job["id"]

    if not worker_alive(config):
        from .worker import Worker
        try:
            Worker(config).run(once=True)
        except Exception:
            # A concurrently starting agent may win the machine lock between the heartbeat check and run().
            if not worker_alive(config):
                raise

    result = wait_for_job(store, job_id, timeout)
    job_dir = config.data_dir / "jobs" / job_id
    diagnostics = result["artifacts"].get("diagnostics")
    payload = {
        "job_id": job_id,
        "state": result["state"],
        "error": result["error"],
        "job_dir": str(job_dir),
        "video": str(job_dir / result["artifacts"]["landscape"])
        if "landscape" in result["artifacts"] else None,
        "diagnostics": str(job_dir / diagnostics) if diagnostics else None,
        "artifacts": result["artifacts"],
    }
    output(payload)
    return 0 if result["state"] == "succeeded" else 1


def main():
    args = parser().parse_args()
    try:
        if args.command == "init":
            from .bootstrap import initialize
            output(initialize(Path(args.config), args.player))
            return 0
        config = load(args.config)
        if args.command == "client-pack":
            from .bootstrap import client_pack
            output({"client_pack": client_pack(config, args.guard_jar)})
            return 0
        if args.command == "doctor":
            return doctor(config, args.launch)
        if args.command == "smoke-test":
            return smoke_test(config, args.seed, args.timeout)
        if args.command in {"submit", "preview"}:
            spec = JobSpec(template=args.template, seed=args.seed, duration_seconds=args.duration,
                           mob_count=args.mobs, language=args.language, title=args.title,
                           formats=["landscape", "short"] if args.format == "both" else [args.format])
            if args.command == "preview":
                from .scenes import compile_plan, write_pack
                plan = compile_plan(spec, 1)
                directory = config.data_dir / "previews" / f"{spec.template}-{spec.seed}"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "plan.json").write_text(plan.json(), encoding="utf-8")
                write_pack(directory / "datapack.zip", plan, config.minecraft.player)
                output({"plan": directory / "plan.json", "datapack": directory / "datapack.zip"})
            else:
                if not 1 <= args.count <= 100:
                    raise ValueError("Batch size must be between 1 and 100")
                store = Store(config.database)
                jobs = [store.submit(spec.model_copy(update={"seed": (spec.seed + i) % 2_147_483_648}))
                        for i in range(args.count)]
                output(jobs)
            return 0
        if args.command in {"status", "cancel", "wait"}:
            store = Store(config.database)
            if args.command == "status":
                output(store.get(args.job_id) if args.job_id else store.list())
            elif args.command == "cancel":
                output(store.cancel(args.job_id))
            else:
                job = wait_for_job(store, args.job_id, args.timeout)
                output(job)
                return 0 if job["state"] == "succeeded" else 1
            return 0
        if args.command in {"worker", "agent"}:
            from .worker import Worker
            worker = Worker(config)
            signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
            signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
            worker.run(once=args.once)
            return 0
        if args.command in {"serve", "start"}:
            import uvicorn
            from .api import create_app
            app = create_app(config)
            worker = None
            if args.command == "start":
                worker = subprocess.Popen([sys.executable, "-m", "mine_video", "--config",
                                           str(Path(args.config).resolve()), "agent"])
            try:
                uvicorn.run(app, host=args.host, port=args.port, access_log=False)
            finally:
                if worker and worker.poll() is None:
                    worker.terminate()
                    try:
                        worker.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        worker.kill()
                        worker.wait()
            return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
