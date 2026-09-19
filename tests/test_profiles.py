import sqlite3
from pathlib import Path

import pytest

from julesctl.config import default_database_path, profile_name
from julesctl.domain.errors import InputError
from julesctl.store import StateStore


def test_profiles_have_distinct_state_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JULESCTL_HOME", str(tmp_path))
    assert default_database_path("default") == tmp_path / "state.db"
    assert default_database_path("automation") == tmp_path / "profiles" / "automation" / "state.db"


def test_invalid_profile_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JULESCTL_PROFILE", "../escape")
    with pytest.raises(InputError, match="JULESCTL_PROFILE"):
        profile_name()


def test_store_rejects_profile_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store = StateStore(path, profile_name="one")
    store.close()
    with pytest.raises(InputError, match="belongs to profile"):
        StateStore(path, profile_name="two")


def test_legacy_database_is_backed_up_and_migrated(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE legacy_marker(value TEXT)")
        connection.execute("INSERT INTO legacy_marker(value) VALUES('present')")
        connection.commit()
    finally:
        connection.close()

    store = StateStore(path, profile_name="default")
    try:
        assert store.schema_version() == 1
        assert store.integrity_check() == ["ok"]
    finally:
        store.close()

    backups = list(tmp_path.glob("state.bak-v0-*.db"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    try:
        assert backup.execute("SELECT value FROM legacy_marker").fetchone()[0] == "present"
    finally:
        backup.close()
