from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from ..domain.errors import ApiError
from ..domain.models import ActivityWire, SessionWire, SourceWire

QueryValue = str | int | float | bool | None
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _default_now() -> datetime:
    return datetime.now(UTC)


class JulesApiClient:
    """Small, security-conscious adapter for the Jules v1alpha REST API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://jules.googleapis.com/v1alpha",
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
        now: Callable[[], datetime] = _default_now,
        max_retry_delay_seconds: float = 60.0,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        parsed = urlsplit(normalized_base_url)
        if parsed.scheme.casefold() != "https":
            raise ValueError("Jules API base_url must use HTTPS")
        if not parsed.hostname:
            raise ValueError("Jules API base_url must include a hostname")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retry_delay_seconds <= 0:
            raise ValueError("max_retry_delay_seconds must be positive")
        self._sleep = sleep
        self._random_value = random_value
        self._now = now
        self._max_retry_delay_seconds = max_retry_delay_seconds
        self._client = httpx.Client(
            base_url=normalized_base_url,
            headers={"X-Goog-Api-Key": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JulesApiClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _parse_retry_after(self, value: str | None) -> float | None:
        if value is None:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        try:
            seconds = int(candidate)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(candidate)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds_value = (retry_at.astimezone(UTC) - self._now().astimezone(UTC)).total_seconds()
            return max(seconds_value, 0.0)
        return float(max(seconds, 0))

    def _error(self, response: httpx.Response) -> ApiError:
        payload: dict[str, object] = {}
        try:
            raw = response.json()
            if isinstance(raw, dict):
                payload = raw
        except ValueError:
            pass
        nested = payload.get("error")
        error = nested if isinstance(nested, dict) else payload
        status = error.get("status") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        return ApiError(
            str(message or f"Jules API returned HTTP {response.status_code}"),
            http_status=response.status_code,
            api_status=str(status) if status else None,
            body=payload,
            retry_after_seconds=self._parse_retry_after(response.headers.get("Retry-After")),
        )

    @staticmethod
    def _json_object(response: httpx.Response, *, context: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(f"{context} response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ApiError(f"{context} response was not an object")
        return payload

    def _request_once(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, QueryValue] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        try:
            request_params = httpx.QueryParams(params) if params else None
            response = self._client.request(
                method,
                path,
                params=request_params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise ApiError(str(exc)) from exc
        if not response.is_success:
            raise self._error(response)
        return response

    @staticmethod
    def _is_retryable(exc: ApiError) -> bool:
        return exc.http_status is None or exc.http_status in _RETRYABLE_STATUS_CODES

    def _retry_delay(self, exc: ApiError, *, backoff_seconds: float) -> float:
        if exc.retry_after_seconds is not None:
            return min(exc.retry_after_seconds, self._max_retry_delay_seconds)
        jitter = backoff_seconds * 0.25 * max(min(self._random_value(), 1.0), 0.0)
        return min(backoff_seconds + jitter, self._max_retry_delay_seconds)

    def _safe_read(
        self,
        path: str,
        *,
        params: dict[str, QueryValue] | None = None,
        attempts: int = 4,
    ) -> httpx.Response:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        backoff_seconds = 0.25
        for index in range(attempts):
            try:
                return self._request_once("GET", path, params=params)
            except ApiError as exc:
                if not self._is_retryable(exc) or index + 1 == attempts:
                    raise
                self._sleep(self._retry_delay(exc, backoff_seconds=backoff_seconds))
                backoff_seconds = min(backoff_seconds * 2, 2.0)
        raise AssertionError("retry loop exhausted without returning or raising")

    def _iter_pages(
        self,
        path: str,
        *,
        item_key: str,
        page_size: int,
        params: dict[str, QueryValue] | None = None,
        max_pages: int = 10_000,
    ) -> Iterator[dict[str, Any]]:
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        token: str | None = None
        seen_tokens: set[str] = set()
        seen_names: set[str] = set()
        base: dict[str, QueryValue] = dict(params or {})
        for _ in range(max_pages):
            query = dict(base)
            query["pageSize"] = page_size
            if token:
                query["pageToken"] = token
            response = self._safe_read(path, params=query)
            payload = self._json_object(response, context=f"list {item_key}")
            items = payload.get(item_key, [])
            if not isinstance(items, list):
                raise ApiError(f"{item_key} was not a list")
            for item in items:
                if not isinstance(item, dict):
                    continue
                identity = item.get("name") or item.get("id")
                if identity is not None:
                    identity_s = str(identity)
                    if identity_s in seen_names:
                        continue
                    seen_names.add(identity_s)
                yield item
            next_token = payload.get("nextPageToken")
            if not next_token:
                return
            token = str(next_token)
            if token in seen_tokens:
                raise ApiError("repeated nextPageToken detected")
            seen_tokens.add(token)
        raise ApiError("pagination limit exceeded before completion")

    def iter_sources(self, *, page_size: int = 100) -> Iterable[SourceWire]:
        for item in self._iter_pages("/sources", item_key="sources", page_size=page_size):
            yield SourceWire.model_validate(item)

    def get_source(self, name: str) -> SourceWire:
        response = self._safe_read("/" + name.lstrip("/"))
        payload = self._json_object(response, context="get source")
        return SourceWire.model_validate(payload)

    def resolve_source(self, repo: str) -> SourceWire:
        owner, name = repo.split("/", 1)
        matches = [
            source
            for source in self.iter_sources()
            if source.github_repo
            and source.github_repo.owner.casefold() == owner.casefold()
            and source.github_repo.repo.casefold() == name.casefold()
        ]
        if len(matches) != 1:
            raise ApiError(
                f"expected exactly one Jules source for {repo}; found {len(matches)}",
                api_status="SOURCE_NOT_UNIQUE",
            )
        return matches[0]

    def iter_sessions(self, *, page_size: int = 100) -> Iterable[SessionWire]:
        for item in self._iter_pages("/sessions", item_key="sessions", page_size=page_size):
            yield SessionWire.model_validate(item)

    def get_session(self, session_id: str) -> SessionWire:
        sid = session_id.removeprefix("sessions/")
        response = self._safe_read(f"/sessions/{sid}")
        payload = self._json_object(response, context="get session")
        return SessionWire.model_validate(payload)

    def create_session(self, body: dict[str, object]) -> SessionWire:
        response = self._request_once("POST", "/sessions", json_body=body)
        payload = self._json_object(response, context="create session")
        try:
            return SessionWire.model_validate(payload)
        except ValidationError as exc:
            raise ApiError("create session response did not contain a usable session") from exc

    def send_message(self, session_id: str, prompt: str) -> None:
        sid = session_id.removeprefix("sessions/")
        self._request_once("POST", f"/sessions/{sid}:sendMessage", json_body={"prompt": prompt})

    def approve_plan(self, session_id: str) -> None:
        sid = session_id.removeprefix("sessions/")
        self._request_once("POST", f"/sessions/{sid}:approvePlan", json_body={})

    def archive_session(self, session_id: str) -> SessionWire:
        sid = session_id.removeprefix("sessions/")
        response = self._request_once("POST", f"/sessions/{sid}:archive", json_body={})
        payload = self._json_object(response, context="archive session")
        return SessionWire.model_validate(payload)

    def unarchive_session(self, session_id: str) -> SessionWire:
        sid = session_id.removeprefix("sessions/")
        response = self._request_once("POST", f"/sessions/{sid}:unarchive", json_body={})
        payload = self._json_object(response, context="unarchive session")
        return SessionWire.model_validate(payload)

    def delete_session(self, session_id: str, *, attempts: int = 4) -> bool:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        sid = session_id.removeprefix("sessions/")
        backoff_seconds = 0.25
        for index in range(attempts):
            try:
                self._request_once("DELETE", f"/sessions/{sid}")
                return True
            except ApiError as exc:
                if exc.http_status == 404:
                    return False
                if not self._is_retryable(exc) or index + 1 == attempts:
                    raise
                self._sleep(self._retry_delay(exc, backoff_seconds=backoff_seconds))
                backoff_seconds = min(backoff_seconds * 2, 2.0)
        raise AssertionError("retry loop exhausted without returning or raising")

    def iter_activities(
        self,
        session_id: str,
        *,
        page_size: int = 100,
        create_time: str | None = None,
    ) -> Iterable[ActivityWire]:
        sid = session_id.removeprefix("sessions/")
        params: dict[str, QueryValue] = {}
        if create_time:
            params["createTime"] = create_time
        for item in self._iter_pages(
            f"/sessions/{sid}/activities",
            item_key="activities",
            page_size=page_size,
            params=params,
        ):
            yield ActivityWire.model_validate(item)

    def get_activity(self, session_id: str, activity_id: str) -> ActivityWire:
        sid = session_id.removeprefix("sessions/")
        aid = activity_id.rsplit("/", 1)[-1]
        response = self._safe_read(f"/sessions/{sid}/activities/{aid}")
        payload = self._json_object(response, context="get activity")
        return ActivityWire.model_validate(payload)
