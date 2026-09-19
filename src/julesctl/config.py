from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path

from .domain.errors import AuthError, InputError

DEFAULT_BASE_URL = "https://jules.googleapis.com/v1alpha"
_PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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
    xdg_state_home = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(xdg_state_home).expanduser() / "julesctl"


def profile_name() -> str:
    value = os.environ.get("JULESCTL_PROFILE", "default").strip() or "default"
    if not _PROFILE_PATTERN.fullmatch(value):
        raise InputError(
            "JULESCTL_PROFILE must start with an alphanumeric character and contain only "
            "letters, numbers, dots, underscores, or hyphens"
        )
    return value


def default_database_path(profile: str | None = None) -> Path:
    selected = profile or profile_name()
    if selected == "default":
        return state_root() / "state.db"
    return state_root() / "profiles" / selected / "state.db"


@dataclass(frozen=True)
class Settings:
    api_key: str
    profile: str = "default"
    base_url: str = DEFAULT_BASE_URL
    database_path: Path = field(default_factory=default_database_path)
    configured_concurrency_limit: int = 15
    configured_rolling_start_limit: int = 100
    new_work_target: int = 12
    reactive_reserve: int = 3
    rolling_start_reserve: int = 5

    @classmethod
    def from_env(cls) -> Settings:
        api_key = os.environ.get("JULES_API_KEY", "").strip()
        if not api_key:
            raise AuthError("JULES_API_KEY is not set")
        selected_profile = profile_name()
        return cls(
            api_key=api_key,
            profile=selected_profile,
            database_path=default_database_path(selected_profile),
        )
