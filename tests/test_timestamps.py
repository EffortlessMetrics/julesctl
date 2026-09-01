from julesctl.timestamps import GoogleTimestamp


def test_nanosecond_timestamp_is_preserved() -> None:
    value = GoogleTimestamp.parse("2026-08-31T12:34:56.123456789Z")
    assert value.raw.endswith("123456789Z")
    assert value.unix_nanoseconds % 1_000_000_000 == 123_456_789
