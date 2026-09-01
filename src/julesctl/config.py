from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://jules.googleapis.com/v1alpha"


def state_root() -> Path:
    override = os.environ.get("JULESCTL_HOME")
    if override:
        return Path(override).expanduser()
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base or Path.home() / "AppData" / "Local") / "julesctl"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "julesctl"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "julesctl"


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    database_path: Path = state_root() / "state.db"
    configured_concurrency_limit: int = 15
    configured_rolling_start_limit: int = 100
    new_work_target: int = 12
    reactive_reserve: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.environ.get("JULES_API_KEY", "").strip()
        if not api_key:
            raise ValueError("JULES_API_KEY is not set")
        return cls(api_key=api_key)
