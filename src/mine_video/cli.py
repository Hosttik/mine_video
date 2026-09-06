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
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
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
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    job = store.get(args.job_id)
                    if not job:
                        raise ValueError("Job not found")
                    if job["state"] in TERMINAL:
                        output(job)
                        return 0 if job["state"] == "succeeded" else 1
                    time.sleep(.5)
                raise TimeoutError("Job is still pending; wait timeout does not cancel it")
            return 0
        if args.command == "worker":
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
                                           str(Path(args.config).resolve()), "worker"])
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
