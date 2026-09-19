from julesctl.timestamps import GoogleTimestamp


def test_nanosecond_timestamp_is_preserved() -> None:
    value = GoogleTimestamp.parse("2026-08-31T12:34:56.123456789Z")
    assert value.raw.endswith("123456789Z")
    assert value.unix_nanoseconds % 1_000_000_000 == 123_456_789


def test_now_round_trips_without_float_rounding() -> None:
    value = GoogleTimestamp.now()
    reparsed = GoogleTimestamp.parse(value.raw)
    assert reparsed.unix_nanoseconds == value.unix_nanoseconds
