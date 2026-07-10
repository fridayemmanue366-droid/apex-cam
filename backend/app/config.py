"""Typed application settings, sourced from environment with sane defaults.

All settings are prefixed with ``APEXCAM_`` in the environment, e.g.
``APEXCAM_LOG_LEVEL=DEBUG``.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APEXCAM_", env_file=".env")

    env: str = "development"
    host: str = "127.0.0.1"
    # Distinctive default port to avoid colliding with other local dev servers
    # (8000/8080 are commonly occupied). Override with APEXCAM_PORT.
    port: int = 8790
    log_level: str = "INFO"

    # Where downloaded model weights live. Never committed to git.
    models_dir: Path = Field(default=Path("models"))

    # Media pipeline defaults (tunable from the Performance tab later).
    target_width: int = 1920
    target_height: int = 1080
    target_fps: int = 30
    use_gpu: bool = True

    # Responsible-use features (see docs/LEGAL.md).
    # label_output burns a small "AI-GENERATED" disclosure onto every output frame.
    # Default OFF (product decision). NOTE: turning it on is the responsible choice
    # for live video calls — it discloses to the other party that the face is AI, and
    # AI-content disclosure is legally required in some regions (e.g. EU AI Act).
    label_output: bool = False
    audit_log: bool = True


settings = Settings()
