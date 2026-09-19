from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

_RFC3339 = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?(?P<tz>Z|[+-]\d{2}:\d{2})$"
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, order=True)
class GoogleTimestamp:
    unix_nanoseconds: int
    raw: str

    @classmethod
    def parse(cls, value: str) -> GoogleTimestamp:
        match = _RFC3339.fullmatch(value)
        if not match:
            raise ValueError(f"invalid RFC3339 timestamp: {value!r}")
        fraction = (match.group("fraction") or "").ljust(9, "0")
        tz = match.group("tz")
        suffix = "Z" if tz == "Z" else tz
        iso = f"{match.group('date')}T{match.group('time')}{suffix}"
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)
        delta = dt - _EPOCH
        seconds = delta.days * 86_400 + delta.seconds
        return cls(seconds * 1_000_000_000 + int(fraction or "0"), value)

    @classmethod
    def now(cls) -> GoogleTimestamp:
        dt = datetime.now(UTC)
        delta = dt - _EPOCH
        ns = (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000
        return cls(ns, dt.isoformat().replace("+00:00", "Z"))
