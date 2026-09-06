import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Minecraft(Section):
    host: str = "127.0.0.1"
    port: int = 25575
    player: str = Field(default="CameraBot", pattern=r"^[A-Za-z0-9_]{3,16}$")
    server_address: str = "localhost:25565"
    server_command: list[str] = Field(default_factory=lambda: ["docker", "compose", "up", "-d", "mc"])
    client_command: list[str] = Field(default_factory=lambda: [
        "prismlauncher", "--launch", "MineVideo", "--server", "localhost:25565",
    ])
    startup_timeout: int = Field(default=180, ge=10, le=900)


class OBS(Section):
    host: str = "127.0.0.1"
    port: int = 4455
    profile: str = "MineVideo"
    scene: str = "MineVideo"
    source: str = "Minecraft"
    command: list[str] = Field(default_factory=lambda: [
        "obs", "--profile", "MineVideo", "--collection", "MineVideo", "--minimize-to-tray",
    ])


class Media(Section):
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    width: int = Field(default=1920, ge=320, le=3840, multiple_of=2)
    height: int = Field(default=1080, ge=180, le=2160, multiple_of=2)
    fps: int = Field(default=30, ge=24, le=60)
    crf: int = Field(default=20, ge=12, le=30)
    preset: str = Field(default="medium", pattern=r"^(ultrafast|veryfast|fast|medium|slow)$")
    minimum_free_gb: float = Field(default=2, ge=0)
    render_timeout: int = Field(default=1800, ge=30, le=7200)


class Settings(Section):
    data_dir: Path = Path("data")
    datapack_dir: Path = Path("server-data/world/datapacks")
    recording_root: Path = Path("recordings")
    guard_url: str = "http://127.0.0.1:8766"
    minecraft: Minecraft = Field(default_factory=Minecraft)
    obs: OBS = Field(default_factory=OBS)
    media: Media = Field(default_factory=Media)
    base_dir: Path = Field(default=Path("."), exclude=True)
    api_token: str = Field(default="", exclude=True, repr=False)
    rcon_password: str = Field(default="", exclude=True, repr=False)
    obs_password: str = Field(default="", exclude=True, repr=False)
    guard_token: str = Field(default="", exclude=True, repr=False)

    @property
    def database(self):
        return self.data_dir / "jobs.sqlite3"


def load(path: str | Path = "mine-video.toml") -> Settings:
    path = Path(path).resolve()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    config = Settings.model_validate(raw)
    config.base_dir = path.parent
    for key in ("data_dir", "datapack_dir", "recording_root"):
        setattr(config, key, (path.parent / getattr(config, key)).resolve())
    # .env is generated locally by `init`: literal KEY=VALUE, no shell evaluation.
    secrets = {}
    env_file = path.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                secrets[key.strip()] = value.strip().strip("\"'")
    for field, name in {
        "api_token": "MV_API_TOKEN", "rcon_password": "RCON_PASSWORD",
        "obs_password": "MV_OBS_PASSWORD", "guard_token": "MV_GUARD_TOKEN",
    }.items():
        setattr(config, field, os.environ.get(name, secrets.get(name, "")))
    return config
