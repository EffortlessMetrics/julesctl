from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import httpx

from .domain.errors import ApiError

DISCOVERY_URL = "https://jules.googleapis.com/$discovery/rest?version=v1alpha"
EXPECTED_OPERATIONS = {
    "sources.list",
    "sources.get",
    "sessions.create",
    "sessions.list",
    "sessions.get",
    "sessions.delete",
    "sessions.sendMessage",
    "sessions.approvePlan",
    "sessions.archive",
    "sessions.unarchive",
    "sessions.activities.list",
    "sessions.activities.get",
}


@dataclass(frozen=True)
class DiscoverySummary:
    revision: str | None
    digest: str
    operations: tuple[str, ...]
    missing_expected: tuple[str, ...]
    unexpected: tuple[str, ...]

    @property
    def compatible(self) -> bool:
        return not self.missing_expected


def _collect_methods(resource: dict[str, object], prefix: str = "") -> set[str]:
    found: set[str] = set()
    methods = resource.get("methods")
    if isinstance(methods, dict):
        for name in methods:
            found.add(f"{prefix}.{name}".lstrip("."))
    resources = resource.get("resources")
    if isinstance(resources, dict):
        for name, child in resources.items():
            if isinstance(child, dict):
                child_prefix = f"{prefix}.{name}".lstrip(".")
                found.update(_collect_methods(child, child_prefix))
    return found


def summarize_discovery(payload: dict[str, object]) -> DiscoverySummary:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    operations: set[str] = set()
    resources = payload.get("resources")
    if isinstance(resources, dict):
        for name, resource in resources.items():
            if isinstance(resource, dict):
                operations.update(_collect_methods(resource, str(name)))
    missing = EXPECTED_OPERATIONS - operations
    unexpected = operations - EXPECTED_OPERATIONS
    revision = payload.get("revision")
    return DiscoverySummary(
        revision=str(revision) if revision is not None else None,
        digest="sha256:" + hashlib.sha256(canonical).hexdigest(),
        operations=tuple(sorted(operations)),
        missing_expected=tuple(sorted(missing)),
        unexpected=tuple(sorted(unexpected)),
    )


def fetch_discovery(
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[dict[str, object], DiscoverySummary]:
    try:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.get(DISCOVERY_URL, headers={"Accept": "application/json"})
            if not response.is_success:
                raise ApiError(
                    f"Jules Discovery returned HTTP {response.status_code}",
                    http_status=response.status_code,
                )
            payload = response.json()
    except ApiError:
        raise
    except httpx.HTTPError as exc:
        raise ApiError(f"Jules Discovery request failed: {exc}") from exc
    except ValueError as exc:
        raise ApiError("Jules Discovery response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ApiError("Jules Discovery response was not an object")
    return payload, summarize_discovery(payload)
