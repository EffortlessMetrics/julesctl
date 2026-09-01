from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from typing import Any

import httpx

from ..domain.errors import ApiError
from ..domain.models import ActivityWire, SessionWire, SourceWire


class JulesApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://jules.googleapis.com/v1alpha",
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Goog-Api-Key": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JulesApiClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _error(response: httpx.Response) -> ApiError:
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
        )

    def _request_once(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        try:
            response = self._client.request(method, path, params=params, json=json_body)
        except httpx.HTTPError as exc:
            raise ApiError(str(exc)) from exc
        if response.is_error:
            raise self._error(response)
        return response

    def _safe_read(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        attempts: int = 4,
    ) -> httpx.Response:
        delay = 0.25
        last: ApiError | None = None
        for index in range(attempts):
            try:
                return self._request_once("GET", path, params=params)
            except ApiError as exc:
                last = exc
                if exc.http_status is not None and exc.http_status not in {429, 500, 502, 503, 504}:
                    raise
                if index + 1 == attempts:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 2.0)
        assert last is not None
        raise last

    def _iter_pages(
        self,
        path: str,
        *,
        item_key: str,
        page_size: int,
        params: dict[str, object] | None = None,
        max_pages: int = 10_000,
    ) -> Iterator[dict[str, Any]]:
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        token: str | None = None
        seen_tokens: set[str] = set()
        seen_names: set[str] = set()
        base = dict(params or {})
        for _ in range(max_pages):
            query = dict(base)
            query["pageSize"] = page_size
            if token:
                query["pageToken"] = token
            response = self._safe_read(path, params=query)
            payload = response.json()
            if not isinstance(payload, dict):
                raise ApiError("list response was not an object")
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
        payload = self._safe_read("/" + name.lstrip("/")).json()
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

    def iter_sessions(
        self,
        *,
        page_size: int = 100,
        filter_value: str | None = None,
    ) -> Iterable[SessionWire]:
        params: dict[str, object] = {}
        if filter_value:
            params["filter"] = filter_value
        for item in self._iter_pages(
            "/sessions", item_key="sessions", page_size=page_size, params=params
        ):
            yield SessionWire.model_validate(item)

    def get_session(self, session_id: str) -> SessionWire:
        sid = session_id.removeprefix("sessions/")
        return SessionWire.model_validate(self._safe_read(f"/sessions/{sid}").json())

    def create_session(self, body: dict[str, object]) -> SessionWire:
        response = self._request_once("POST", "/sessions", json_body=body)
        return SessionWire.model_validate(response.json())

    def send_message(self, session_id: str, prompt: str) -> None:
        sid = session_id.removeprefix("sessions/")
        self._request_once("POST", f"/sessions/{sid}:sendMessage", json_body={"prompt": prompt})

    def approve_plan(self, session_id: str) -> None:
        sid = session_id.removeprefix("sessions/")
        self._request_once("POST", f"/sessions/{sid}:approvePlan", json_body={})

    def archive_session(self, session_id: str) -> SessionWire:
        sid = session_id.removeprefix("sessions/")
        response = self._request_once("POST", f"/sessions/{sid}:archive", json_body={})
        return SessionWire.model_validate(response.json())

    def unarchive_session(self, session_id: str) -> SessionWire:
        sid = session_id.removeprefix("sessions/")
        response = self._request_once("POST", f"/sessions/{sid}:unarchive", json_body={})
        return SessionWire.model_validate(response.json())

    def delete_session(self, session_id: str) -> bool:
        sid = session_id.removeprefix("sessions/")
        delay = 0.25
        for index in range(4):
            try:
                self._request_once("DELETE", f"/sessions/{sid}")
                return True
            except ApiError as exc:
                if exc.http_status == 404:
                    return False
                if exc.http_status not in {429, 500, 502, 503, 504} or index == 3:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 2.0)
        return False

    def iter_activities(
        self,
        session_id: str,
        *,
        page_size: int = 100,
        filter_value: str | None = None,
    ) -> Iterable[ActivityWire]:
        sid = session_id.removeprefix("sessions/")
        params: dict[str, object] = {}
        if filter_value:
            params["filter"] = filter_value
        for item in self._iter_pages(
            f"/sessions/{sid}/activities",
            item_key="activities",
            page_size=page_size,
            params=params,
        ):
            yield ActivityWire.model_validate(item)
