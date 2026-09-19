from __future__ import annotations

from dataclasses import dataclass

_EXECUTING = {"QUEUED", "PLANNING", "IN_PROGRESS"}
_PAUSED = {"PAUSED"}
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


@dataclass(frozen=True)
class ClassifiedState:
    raw: str
    lifecycle: str
    action_required: str


def classify_state(raw: str | None) -> ClassifiedState:
    value = raw or "STATE_UNSPECIFIED"
    if value in _EXECUTING:
        return ClassifiedState(value, "executing", "none")
    if value == "AWAITING_PLAN_APPROVAL":
        return ClassifiedState(value, "actionable", "plan_approval")
    if value == "AWAITING_USER_FEEDBACK":
        return ClassifiedState(value, "actionable", "user_feedback")
    if value in _PAUSED:
        return ClassifiedState(value, "paused", "none")
    if value in _TERMINAL:
        return ClassifiedState(value, "terminal", "none")
    return ClassifiedState(value, "unknown", "unknown")
