import json
import os
import secrets
import sys
import zipfile
from importlib.resources import files
from pathlib import Path

from .config import Minecraft, load
from .models import JobSpec
from .scenes import compile_plan, write_pack


def initialize(config_path: Path, player: str):
    Minecraft(player=player)  # Validate before writing any files.
    base = config_path.resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    if config_path.exists() or (base / ".env").exists():
        raise RuntimeError("Configuration already exists; edit it without overwriting local secrets")
    settings = files("mine_video").joinpath("templates/mine-video.toml").read_text(encoding="utf-8")
    settings = settings.replace('player = "CameraBot"', f'player = "{player}"')
    if sys.platform == "darwin":
        settings = settings.replace('["prismlauncher",', '["/Applications/Prism Launcher.app/Contents/MacOS/prismlauncher",')
        settings = settings.replace('["obs",', '["/Applications/OBS.app/Contents/MacOS/OBS",')
    config_path.write_text(settings, encoding="utf-8")
    env = (
        "# Set EULA=TRUE after reading https://aka.ms/MinecraftEULA\nEULA=FALSE\n"
        f"CAMERA_PLAYER={player}\nRCON_PASSWORD={secrets.token_urlsafe(32)}\n"
        f"MV_API_TOKEN={secrets.token_urlsafe(32)}\nMV_OBS_PASSWORD={secrets.token_urlsafe(32)}\n"
        f"MV_GUARD_TOKEN={secrets.token_urlsafe(32)}\n"
    )
    env_path = base / ".env"
    descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(env)
    compose = base / "compose.yml"
    if not compose.exists():
        compose.write_text(files("mine_video").joinpath("templates/compose.yml").read_text(encoding="utf-8"),
                           encoding="utf-8")
    config = load(config_path)
    for path in (config.data_dir, config.recording_root, config.datapack_dir):
        path.mkdir(parents=True, exist_ok=True)
    write_pack(config.datapack_dir / "minevideo.zip", compile_plan(JobSpec(), 0), player)
    return {"config": str(config_path.resolve()), "next": "Configure OBS, import a client pack, then run doctor"}


def client_pack(config, guard_jar: Path):
    if not guard_jar.is_file():
        raise RuntimeError("Build client-guard with Gradle or download its CI artifact first")
    with zipfile.ZipFile(guard_jar) as jar:
        manifest = json.loads(jar.read("fabric.mod.json"))
        if manifest.get("id") != "minevideo-capture":
            raise RuntimeError("Expected the MineVideo capture guard JAR")
    target = config.data_dir / "MineVideo-client.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mmc-pack.json", json.dumps({"formatVersion": 1, "components": [
            {"uid": "net.minecraft", "version": "1.21.4", "important": True},
            {"uid": "net.fabricmc.fabric-loader", "version": "0.16.10"},
        ]}, indent=2))
        archive.writestr("instance.cfg", "\n".join([
            "[General]", "InstanceType=OneSix", "name=MineVideo", "OverrideMemory=true",
            "MinMemAlloc=2048", "MaxMemAlloc=4096", "OverrideWindow=true",
            "MinecraftWinWidth=1920", "MinecraftWinHeight=1080",
        ]) + "\n")
        archive.writestr(".minecraft/options.txt", "\n".join([
            "version:4189", "pauseOnLostFocus:false", "maxFps:60", "renderDistance:12",
            "simulationDistance:8", "fullscreen:false", "bobView:false", "fov:0.0",
            "soundCategory_music:0.0", "soundCategory_master:0.8", "tutorialStep:none",
            "onboardAccessibility:false", "skipMultiplayerWarning:true", "joinedFirstServer:true",
        ]) + "\n")
        archive.writestr(".minecraft/config/minevideo-capture.json", json.dumps({
            "token": config.guard_token, "port": 8766, "player": config.minecraft.player,
            "serverAddress": config.minecraft.server_address,
        }, indent=2))
        archive.write(guard_jar, ".minecraft/mods/minevideo-capture.jar")
    # The local pack contains a guard token and must never be published as a release.
    target.chmod(0o600)
    return target
