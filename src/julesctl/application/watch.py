from __future__ import annotations

import time
from collections.abc import Callable, Iterator

from ..api.client import JulesApiClient
from ..domain.errors import IndeterminateError
from ..domain.states import classify_state
from ..store import StateStore
from .reconcile import reconcile_session_activities


def watch_session(
    api: JulesApiClient,
    store: StateStore,
    session_id: str,
    *,
    origin: str,
    poll_interval_seconds: float = 3.0,
    settle_seconds: float = 1.0,
    timeout_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_polls: int | None = None,
) -> Iterator[dict[str, object]]:
    """Yield new activities and state changes until the session becomes terminal."""

    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must not be negative")
    if settle_seconds < 0:
        raise ValueError("settle_seconds must not be negative")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_polls is not None and max_polls < 1:
        raise ValueError("max_polls must be at least 1")

    started = monotonic()
    previous_state: str | None = None
    polls = 0
    while True:
        if timeout_seconds is not None and monotonic() - started >= timeout_seconds:
            raise IndeterminateError(
                f"watch timed out before session {session_id} reached a terminal state"
            )
        session = api.get_session(session_id)
        state = classify_state(session.state)
        if state.raw != previous_state:
            previous_state = state.raw
            yield {
                "type": "state",
                "session_id": session.id,
                "state": state.raw,
                "lifecycle": state.lifecycle,
                "action_required": state.action_required,
            }
        yield from reconcile_session_activities(
            api,
            store,
            session.id,
            origin=origin,
        )
        polls += 1
        if state.lifecycle == "terminal":
            if settle_seconds:
                sleep(settle_seconds)
            yield from reconcile_session_activities(
                api,
                store,
                session.id,
                origin=origin,
            )
            yield {
                "type": "terminal",
                "session_id": session.id,
                "state": state.raw,
            }
            return
        if max_polls is not None and polls >= max_polls:
            return
        if poll_interval_seconds:
            sleep(poll_interval_seconds)
