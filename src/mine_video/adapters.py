import base64
import hashlib
import io
import json
import re
import shutil
import urllib.request
from pathlib import Path

from PIL import Image, ImageStat

from .config import Settings
from .runtime import wait_for
from .scenes import DIMENSION, MC_VERSION


class Minecraft:
    def __init__(self, config: Settings, factory=None):
        self.config, self.client = config, None
        self.factory = factory

    def connect(self):
        if self.client:
            self.close()
        if not self.config.rcon_password:
            raise RuntimeError("RCON_PASSWORD is missing")
        if self.factory is None:
            from mcrcon import MCRcon
            self.factory = MCRcon
        self.client = self.factory(self.config.minecraft.host, self.config.rcon_password,
                                   port=self.config.minecraft.port, timeout=5)
        try:
            self.client.connect()
            response = self.command("version")
            if MC_VERSION not in response:
                raise RuntimeError(f"Expected Paper {MC_VERSION}; server reported {response[:150]}")
            self.command(f"execute in {DIMENSION} run seed")
        except Exception:
            self.close()
            raise
        return True

    def command(self, command):
        if not self.client:
            raise RuntimeError("RCON not connected")
        response = self.client.command(command)
        if re.search(r"Unknown or incomplete command|Unknown function|Unknown dimension|Incorrect argument|"
                     r"Failed to execute|Invalid|Unknown scoreboard objective", response, re.I):
            raise RuntimeError(f"Minecraft rejected {command.split()[0]}: {response[:250]}")
        return response

    def score(self, name):
        response = self.command(f"scoreboard players get #{name} mv")
        match = re.search(r"has (-?\d+) \[mv\]", response)
        if not match:
            raise RuntimeError(f"Invalid scoreboard response: {response[:150]}")
        return int(match.group(1))

    def online(self):
        self.command(f"execute store success score #online mv if entity @a[name={self.config.minecraft.player}]")
        if self.score("online") != 1:
            raise RuntimeError("Minecraft camera account is not connected")
        return True

    def deploy(self, nonce, check_cancel):
        self.command("reload")
        def loaded():
            if self.score("nonce") != nonce:
                raise RuntimeError("New datapack did not load; inspect server logs")
            return True
        wait_for(loaded, timeout=30, check_cancel=check_cancel)
        # Forced chunks are requested asynchronously; filling in the same tick can silently fail.
        self.command(f"execute in {DIMENSION} run forceload add -48 -48 48 48")
        conditions = " ".join(f"if loaded {x * 16} 80 {z * 16}" for x in range(-2, 2) for z in range(-2, 2))
        def chunks_loaded():
            self.command(f"execute in {DIMENSION} store success score #loaded mv {conditions}")
            if self.score("loaded") != 1:
                raise RuntimeError("Waiting for studio chunks")
            return True
        wait_for(chunks_loaded, timeout=45, check_cancel=check_cancel)
        self.command("function minevideo:prepare")
        if self.score("ready") != 1:
            raise RuntimeError("Scene preparation did not complete")

    def start(self):
        self.command("function minevideo:start")
        if self.score("run") != 1:
            raise RuntimeError("Scene failed to start")

    def status(self):
        return {key: self.score(key) for key in ("nonce", "run", "tick", "result")}

    def cleanup(self):
        self.command("function minevideo:cleanup")

    def close(self):
        if self.client:
            try:
                self.client.disconnect()
            finally:
                self.client = None


class CaptureGuard:
    def __init__(self, config):
        self.config = config

    def status(self):
        request = urllib.request.Request(self.config.guard_url + "/health", headers={
            "Authorization": "Bearer " + self.config.guard_token,
        })
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    def connect_game(self):
        request = urllib.request.Request(self.config.guard_url + "/connect", method="POST", data=b"", headers={
            "Authorization": "Bearer " + self.config.guard_token,
        })
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    def ready(self, camera=None):
        state = self.status()
        checks = {
            "protocol": state.get("protocol") == 1,
            "account": state.get("player") == self.config.minecraft.player,
            "server": state.get("server") == self.config.minecraft.server_address,
            "rendering": state.get("renderAgeMs", 99999) < 2500,
            "world": state.get("world") == DIMENSION,
            "HUD": state.get("hudHidden") is True,
            "screen": state.get("screen") is None,
        }
        if camera:
            expected = [float(x) for x in camera.split()[:3]]
            actual = state.get("position", [])
            checks["position"] = len(actual) == 3 and all(abs(a - b) < 0.5 for a, b in zip(actual, expected))
        failed = [key for key, value in checks.items() if not value]
        if failed:
            raise RuntimeError("Capture guard not ready: " + ", ".join(failed))
        return state


class OBS:
    def __init__(self, config: Settings, factory=None):
        self.config, self.client, self.factory = config, None, factory

    def connect(self):
        self.close()
        if not self.config.obs_password:
            raise RuntimeError("MV_OBS_PASSWORD is missing")
        if self.factory is None:
            from obsws_python import ReqClient
            self.factory = ReqClient
        self.client = self.factory(host=self.config.obs.host, port=self.config.obs.port,
                                   password=self.config.obs_password, timeout=5)
        self.call("GetVersion")
        return True

    def call(self, kind, data=None):
        return self.client.send(kind, data, raw=True)

    def preflight(self):
        cfg = self.config.obs
        if self.call("GetProfileList")["currentProfileName"] != cfg.profile:
            raise RuntimeError("Select the dedicated MineVideo OBS profile")
        if self.call("GetRecordStatus")["outputActive"]:
            raise RuntimeError("OBS is already recording; refusing to take over")
        if self.call("GetStreamStatus")["outputActive"]:
            raise RuntimeError("Dedicated OBS instance must not be streaming")
        items = self.call("GetSceneItemList", {"sceneName": cfg.scene})["sceneItems"]
        if not any(i["sourceName"] == cfg.source and i["sceneItemEnabled"] for i in items):
            raise RuntimeError("The configured Minecraft source is absent or disabled in the OBS scene")
        self.call("SetCurrentProgramScene", {"sceneName": cfg.scene})
        return self.sample()

    def sample(self):
        if self.call("GetCurrentProgramScene")["currentProgramSceneName"] != self.config.obs.scene:
            raise RuntimeError("OBS scene changed during the job")
        response = self.call("GetSourceScreenshot", {
            "sourceName": self.config.obs.source, "imageFormat": "png", "imageWidth": 320,
        })
        png = base64.b64decode(response["imageData"].split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(png)) as image:
            gray = image.convert("L")
            stats = ImageStat.Stat(gray)
            brightness, deviation = stats.mean[0], stats.stddev[0]
            digest = hashlib.sha256(gray.tobytes()).hexdigest()
        if brightness < 3 or deviation < 2:
            raise RuntimeError("Minecraft capture is black or blank; check the OBS source and window permission")
        return {"sha256": digest, "brightness": round(brightness, 2), "deviation": round(deviation, 2)}

    def start(self):
        self.call("StartRecord")
        return self.record_seconds()

    def record_seconds(self):
        state = self.call("GetRecordStatus")
        if not state["outputActive"] or state.get("outputPaused"):
            raise RuntimeError("OBS recording stopped or paused unexpectedly")
        return state["outputDuration"] / 1000

    def stop(self):
        return Path(self.call("StopRecord")["outputPath"])

    def close(self):
        if self.client:
            try:
                self.client.disconnect()
            finally:
                self.client = None


def copy_recording(source: Path, root: Path, target_dir: Path, check_cancel=lambda: None):
    source, root = source.resolve(), root.resolve()
    if not source.is_relative_to(root):
        raise RuntimeError("OBS output is outside recording_root; set the same local path in OBS and config")
    previous = None
    stable = 0
    def complete():
        nonlocal previous, stable
        size = source.stat().st_size
        stable = stable + 1 if size and size == previous else 0
        previous = size
        if stable < 2:
            raise RuntimeError("Waiting for recording file to settle")
        return True
    wait_for(complete, timeout=15, check_cancel=check_cancel)
    target = target_dir / ("raw" + source.suffix.lower())
    temp = target.with_suffix(target.suffix + ".tmp")
    with source.open("rb") as src, temp.open("wb") as dst:
        while chunk := src.read(4 * 1024 * 1024):
            check_cancel()
            dst.write(chunk)
    if temp.stat().st_size != source.stat().st_size:
        raise RuntimeError("Recording changed while copying")
    shutil.move(temp, target)
    return target
