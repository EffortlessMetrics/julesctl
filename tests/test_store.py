from pathlib import Path

import pytest

from julesctl.domain.errors import InputError
from julesctl.store import StateStore


def test_dispatch_key_conflict_fails_closed(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "s.db")
    try:
        attempt = {"title": "x", "prompt_sha256": "sha256:x"}
        store.reserve_work(
            dispatch_key="k",
            fingerprint="a",
            attempt_id="1",
            attempt=attempt,
        )
        with pytest.raises(InputError, match="different fingerprint"):
            store.reserve_work(
                dispatch_key="k",
                fingerprint="b",
                attempt_id="2",
                attempt=attempt,
            )
    finally:
        store.close()
