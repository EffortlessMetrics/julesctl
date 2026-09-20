from __future__ import annotations

from types import SimpleNamespace

from julesctl.controller import JulesController
from julesctl.domain.models import ActivityWire, SessionWire


class ResultApi:
    def get_session(self, session_id: str) -> SessionWire:
        assert session_id == "1"
        return SessionWire.model_validate({"name": "sessions/1", "id": "1"})

    def iter_activities(self, session_id: str):
        assert session_id == "1"
        return iter(
            [
                ActivityWire.model_validate(
                    {
                        "name": "sessions/1/activities/a",
                        "id": "a",
                        "artifacts": [
                            {
                                "media": {
                                    "mimeType": "image/png",
                                    "data": "aGVsbG8=",
                                }
                            }
                        ],
                    }
                )
            ]
        )


class ResultStore:
    @staticmethod
    def managed_session_ids() -> set[str]:
        return {"1"}

    @staticmethod
    def upsert_session(_row: dict[str, object]) -> None:
        return None


def test_controller_session_result_redacts_inline_media() -> None:
    controller = JulesController(  # type: ignore[arg-type]
        SimpleNamespace(api=ResultApi(), store=ResultStore(), settings=SimpleNamespace())
    )
    result = controller.session_result("1")
    activities = result["activities"]
    assert isinstance(activities, list)
    media = activities[0]["artifacts"][0]["media"]
    assert "data" not in media
    assert media == {
        "mimeType": "image/png",
        "inlineDataOmitted": True,
        "decodedBytes": 5,
    }
