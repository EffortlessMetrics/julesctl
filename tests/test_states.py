from julesctl.domain.states import classify_state


def test_unknown_state_is_preserved() -> None:
    result = classify_state("SUSPENDED_BY_POLICY")
    assert result.raw == "SUSPENDED_BY_POLICY"
    assert result.lifecycle == "unknown"


def test_cancelled_is_terminal_observation() -> None:
    assert classify_state("CANCELLED").lifecycle == "terminal"
