from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Template = Literal["mob_arena", "tnt_chain", "tower_build"]
VideoFormat = Literal["landscape", "short"]
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


class JobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    template: Template = "mob_arena"
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    duration_seconds: int = Field(default=40, ge=15, le=180)
    mob_count: int = Field(default=12, ge=2, le=24)
    formats: list[VideoFormat] = Field(default_factory=lambda: ["landscape", "short"], min_length=1, max_length=2)
    language: Literal["en", "ru"] = "en"
    title: str | None = Field(default=None, min_length=1, max_length=90)

    @field_validator("formats")
    @classmethod
    def unique_formats(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("formats must be unique")
        return value

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if value is not None and (not value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("title must contain visible text and no control characters")
        return value
