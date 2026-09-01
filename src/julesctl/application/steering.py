from __future__ import annotations

from typing import Any

from ..api.client import JulesApiClient
from ..domain.errors import ApiError, IndeterminateError


def _uncertain(exc: ApiError) -> bool:
    if exc.http_status is None:
        return True
    return exc.http_status in {408, 409, 425, 429} or exc.http_status >= 500 or (
        exc.http_status == 400 and exc.api_status == "FAILED_PRECONDITION"
    )


def message_once(api: JulesApiClient, session_id: str, prompt: str) -> dict[str, object]:
    try:
        api.send_message(session_id, prompt)
        return {"outcome": "completed", "session_id": session_id, "reconciled": False}
    except ApiError as exc:
        if not _uncertain(exc):
            raise
        matches = 0
        for activity in api.iter_activities(session_id):
            message: Any = activity.user_messaged
            if isinstance(message, dict) and message.get("userMessage") == prompt:
                matches += 1
        if matches == 1:
            return {
                "outcome": "reconciled",
                "session_id": session_id,
                "reconciled": True,
                "original_http_status": exc.http_status,
                "original_api_status": exc.api_status,
            }
        raise IndeterminateError(
            f"message outcome is indeterminate; matching userMessaged activities={matches}"
        ) from exc


def approve_once(api: JulesApiClient, session_id: str) -> dict[str, object]:
    try:
        api.approve_plan(session_id)
        return {"outcome": "completed", "session_id": session_id, "reconciled": False}
    except ApiError as exc:
        if not _uncertain(exc):
            raise
        session = api.get_session(session_id)
        approved = any(activity.plan_approved is not None for activity in api.iter_activities(session_id))
        moved = session.state not in {None, "STATE_UNSPECIFIED", "AWAITING_PLAN_APPROVAL"}
        if approved or moved:
            return {
                "outcome": "reconciled",
                "session_id": session_id,
                "reconciled": True,
                "observed_state": session.state,
                "original_http_status": exc.http_status,
                "original_api_status": exc.api_status,
            }
        raise IndeterminateError("plan approval outcome is indeterminate") from exc


def archive_once(api: JulesApiClient, session_id: str) -> dict[str, object]:
    try:
        session = api.archive_session(session_id)
        return {"outcome": "completed", "session_id": session_id, "archived": session.archived}
    except ApiError as exc:
        if not _uncertain(exc):
            raise
        session = api.get_session(session_id)
        if session.archived is True:
            return {"outcome": "reconciled", "session_id": session_id, "archived": True}
        raise IndeterminateError("archive outcome is indeterminate") from exc


def unarchive_once(api: JulesApiClient, session_id: str) -> dict[str, object]:
    try:
        session = api.unarchive_session(session_id)
        return {"outcome": "completed", "session_id": session_id, "archived": session.archived}
    except ApiError as exc:
        if not _uncertain(exc):
            raise
        session = api.get_session(session_id)
        if session.archived is False:
            return {"outcome": "reconciled", "session_id": session_id, "archived": False}
        raise IndeterminateError("unarchive outcome is indeterminate") from exc
