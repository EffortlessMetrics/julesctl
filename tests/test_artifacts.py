from __future__ import annotations

from types import SimpleNamespace

from julesctl.application.artifacts import (
    activity_result,
    collect_artifacts,
    select_patch,
    select_pull_request,
)
from julesctl.client import JulesClient
from julesctl.domain.errors import InputError
from julesctl.domain.models import ActivityWire, SessionWire


def _session() -> SessionWire:
    return SessionWire.model_validate(
        {
            "name": "sessions/1",
            "id": "1",
            "outputs": [
                {
                    "pullRequest": {
                        "url": "https://github.com/acme/repo/pull/1",
                        "title": "Fix",
                        "baseRef": "main",
                        "headRef": "jules/fix",
                    }
                },
                {
                    "changeSet": {
                        "source": "sources/github/acme/repo",
                        "gitPatch": {
                            "baseCommitId": "abc",
                            "unidiffPatch": "diff --git a/a b/a\n",
                            "suggestedCommitMessage": "fix: a",
                        },
                    }
                },
            ],
        }
    )


def _activities() -> list[ActivityWire]:
    return [
        ActivityWire.model_validate(
            {
                "name": "sessions/1/activities/a",
                "id": "a",
                "artifacts": [
                    {
                        "changeSet": {
                            "source": "sources/github/acme/repo",
                            "gitPatch": {
                                "baseCommitId": "def",
                                "unidiffPatch": "diff --git a/b b/b\n",
                            },
                        }
                    },
                    {
                        "bashOutput": {
                            "command": "pytest",
                            "output": "2 passed",
                            "exitCode": 0,
                        }
                    },
                    {
                        "media": {
                            "mimeType": "image/png",
                            "data": "aGVsbG8=",
                            "futureMediaField": "retained",
                        },
                        "futureArtifact": {"x": 1},
                    },
                ],
            }
        )
    ]


def test_collect_artifacts_normalizes_outputs_without_media_data() -> None:
    result = collect_artifacts(_session(), _activities())
    assert result["pull_requests"] == [
        {
            "url": "https://github.com/acme/repo/pull/1",
            "title": "Fix",
            "baseRef": "main",
            "headRef": "jules/fix",
        }
    ]
    patches = result["patches"]
    assert isinstance(patches, list)
    assert [item["base_commit_id"] for item in patches] == ["abc", "def"]
    assert result["bash_outputs"] == [
        {
            "activity_name": "sessions/1/activities/a",
            "command": "pytest",
            "output": "2 passed",
            "exitCode": 0,
        }
    ]
    assert result["media"] == [
        {
            "activity_name": "sessions/1/activities/a",
            "mime_type": "image/png",
            "decoded_bytes": 5,
            "inline_data_omitted": True,
        }
    ]
    assert result["unknown_artifact_fields"] == [
        {
            "activity_name": "sessions/1/activities/a",
            "fields": ["futureArtifact"],
        }
    ]


def test_activity_result_redacts_media_body_and_preserves_metadata() -> None:
    result = activity_result(_activities()[0])
    artifacts = result["artifacts"]
    assert isinstance(artifacts, list)
    media = artifacts[2]["media"]
    assert "data" not in media
    assert media == {
        "mimeType": "image/png",
        "futureMediaField": "retained",
        "inlineDataOmitted": True,
        "decodedBytes": 5,
    }


class ResultApi:
    def get_session(self, session_id: str) -> SessionWire:
        assert session_id == "1"
        return _session()

    def iter_activities(self, session_id: str):
        assert session_id == "1"
        return iter(_activities())


class ResultController:
    def __init__(self) -> None:
        self.ctx = SimpleNamespace(
            api=ResultApi(),
            store=SimpleNamespace(managed_session_ids=lambda: {"1"}),
        )

    @staticmethod
    def _remember_session(session: SessionWire, *, origin: str) -> None:
        assert session.id == "1"
        assert origin == "managed"

    @staticmethod
    def normalize_session(session: SessionWire, *, origin: str) -> dict[str, object]:
        return {"id": session.id, "origin": origin}


def test_client_result_never_reintroduces_inline_media() -> None:
    client = JulesClient(ResultController())  # type: ignore[arg-type]
    result = client.result("1")
    activities = result["activities"]
    assert isinstance(activities, list)
    media = activities[0]["artifacts"][2]["media"]
    assert "data" not in media
    assert media["inlineDataOmitted"] is True


def test_duplicate_patch_is_deduplicated() -> None:
    session = _session()
    activity = ActivityWire.model_validate(
        {
            "name": "sessions/1/activities/a",
            "artifacts": [session.outputs[1].model_dump(by_alias=True, exclude_none=True)],
        }
    )
    result = collect_artifacts(session, [activity])
    assert len(result["patches"]) == 1


def test_patch_and_pr_selectors_are_explicit() -> None:
    result = collect_artifacts(_session(), _activities())
    assert select_patch(result)["base_commit_id"] == "def"
    assert select_patch(result, index=0)["base_commit_id"] == "abc"
    assert select_pull_request(result)["url"].endswith("/pull/1")


def test_selectors_reject_missing_or_bad_indices() -> None:
    for selector in (select_patch, select_pull_request):
        try:
            selector({})
        except InputError:
            pass
        else:
            raise AssertionError("missing output should fail")
    try:
        select_patch(collect_artifacts(_session(), []), index=9)
    except InputError:
        pass
    else:
        raise AssertionError("out-of-range patch index should fail")
