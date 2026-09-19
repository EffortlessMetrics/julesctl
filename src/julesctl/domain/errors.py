from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperationError:
    kind: str
    message: str
    http_status: int | None = None
    api_status: str | None = None
    transient: bool = False
    safe_to_retry: bool = False
    reconcile_required: bool = False
    candidate_session_ids: tuple[str, ...] = ()


class JulesCtlError(RuntimeError):
    exit_code = 4


class InputError(JulesCtlError):
    exit_code = 2


class AuthError(JulesCtlError):
    exit_code = 3


class AdmissionError(JulesCtlError):
    exit_code = 7


class IndeterminateError(JulesCtlError):
    exit_code = 5


class ApiError(JulesCtlError):
    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        api_status: str | None = None,
        body: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.api_status = api_status
        self.body = body or {}

    @property
    def create_outcome_uncertain(self) -> bool:
        if self.http_status is None:
            return True
        if self.http_status in {408, 409, 425, 429} or self.http_status >= 500:
            return True
        return self.http_status == 400 and self.api_status == "FAILED_PRECONDITION"
