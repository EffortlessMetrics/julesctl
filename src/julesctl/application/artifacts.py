from __future__ import annotations

import base64
from typing import Any

from ..domain.errors import InputError
from ..domain.models import ActivityWire, ChangeSetWire, PullRequestWire, SessionWire


def _pull_request(value: PullRequestWire) -> dict[str, object]:
    return value.model_dump(by_alias=True, exclude_none=True)


def _change_set(
    value: ChangeSetWire,
    *,
    surface: str,
    activity_name: str | None = None,
) -> dict[str, object] | None:
    patch = value.git_patch
    if patch is None:
        return None
    result: dict[str, object] = {
        "source": value.source,
        "base_commit_id": patch.base_commit_id,
        "unidiff_patch": patch.unidiff_patch,
        "suggested_commit_message": patch.suggested_commit_message,
        "surface": surface,
    }
    if activity_name is not None:
        result["activity_name"] = activity_name
    return result


def _decoded_media_bytes(value: dict[str, Any]) -> int | None:
    encoded = value.get("data")
    if not isinstance(encoded, str):
        return None
    try:
        return len(base64.b64decode(encoded, validate=True))
    except (ValueError, TypeError):
        return None


def _media_summary(value: dict[str, Any], *, activity_name: str) -> dict[str, object]:
    return {
        "activity_name": activity_name,
        "mime_type": value.get("mimeType"),
        "decoded_bytes": _decoded_media_bytes(value),
        "inline_data_omitted": True,
    }


def _redacted_media(value: dict[str, Any]) -> dict[str, object]:
    """Preserve all server metadata while isolating controller-owned redaction evidence."""

    metadata = {key: item for key, item in value.items() if key != "data"}
    redaction: dict[str, object] = {"inline_data_omitted": True}
    decoded_bytes = _decoded_media_bytes(value)
    if decoded_bytes is not None:
        redaction["decoded_bytes"] = decoded_bytes
    return {
        "metadata": metadata,
        "redaction": redaction,
    }


def activity_result(activity: ActivityWire) -> dict[str, object]:
    """Return one activity without embedding documented inline media bodies."""

    result = activity.model_dump(by_alias=True, exclude_none=True)
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list):
        return result

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        media = artifact.get("media")
        if not isinstance(media, dict) or "data" not in media:
            continue
        artifact["media"] = _redacted_media(media)
    return result


def collect_artifacts(
    session: SessionWire,
    activities: list[ActivityWire],
) -> dict[str, object]:
    """Collect normalized lightweight result evidence without inlining media."""

    pull_requests: list[dict[str, object]] = []
    patches: list[dict[str, object]] = []
    bash_outputs: list[dict[str, object]] = []
    media: list[dict[str, object]] = []
    unknown_artifact_fields: list[dict[str, object]] = []
    seen_prs: set[str] = set()
    seen_patches: set[tuple[object, object, object]] = set()

    for output in session.outputs:
        if output.pull_request is not None:
            pr_item = _pull_request(output.pull_request)
            pr_identity = str(pr_item.get("url") or pr_item)
            if pr_identity not in seen_prs:
                seen_prs.add(pr_identity)
                pull_requests.append(pr_item)
        if output.change_set is not None:
            patch_item = _change_set(output.change_set, surface="session_output")
            if patch_item is not None:
                patch_identity = (
                    patch_item.get("source"),
                    patch_item.get("base_commit_id"),
                    patch_item.get("unidiff_patch"),
                )
                if patch_identity not in seen_patches:
                    seen_patches.add(patch_identity)
                    patches.append(patch_item)

    for activity in activities:
        for artifact in activity.artifacts:
            if artifact.change_set is not None:
                activity_patch_item = _change_set(
                    artifact.change_set,
                    surface="activity",
                    activity_name=activity.name,
                )
                if activity_patch_item is not None:
                    activity_patch_identity = (
                        activity_patch_item.get("source"),
                        activity_patch_item.get("base_commit_id"),
                        activity_patch_item.get("unidiff_patch"),
                    )
                    if activity_patch_identity not in seen_patches:
                        seen_patches.add(activity_patch_identity)
                        patches.append(activity_patch_item)
            if artifact.bash_output is not None:
                bash_outputs.append(
                    {
                        "activity_name": activity.name,
                        **artifact.bash_output,
                    }
                )
            if artifact.media is not None:
                media.append(_media_summary(artifact.media, activity_name=activity.name))
            if artifact.model_extra:
                unknown_artifact_fields.append(
                    {
                        "activity_name": activity.name,
                        "fields": sorted(str(key) for key in artifact.model_extra),
                    }
                )

    return {
        "pull_requests": pull_requests,
        "patches": patches,
        "bash_outputs": bash_outputs,
        "media": media,
        "unknown_artifact_fields": unknown_artifact_fields,
    }


def select_patch(result: dict[str, object], index: int = -1) -> dict[str, object]:
    raw = result.get("patches")
    if not isinstance(raw, list) or not raw:
        raise InputError("session has no patch artifact")
    try:
        value = raw[index]
    except IndexError as exc:
        raise InputError(f"patch index {index} is out of range") from exc
    if not isinstance(value, dict):
        raise InputError("selected patch artifact is malformed")
    patch = value.get("unidiff_patch")
    if not isinstance(patch, str) or not patch:
        raise InputError("selected patch artifact has no unidiff content")
    return value


def select_pull_request(result: dict[str, object], index: int = -1) -> dict[str, object]:
    raw = result.get("pull_requests")
    if not isinstance(raw, list) or not raw:
        raise InputError("session has no pull request output")
    try:
        value = raw[index]
    except IndexError as exc:
        raise InputError(f"pull request index {index} is out of range") from exc
    if not isinstance(value, dict):
        raise InputError("selected pull request output is malformed")
    return value
