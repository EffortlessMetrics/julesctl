from __future__ import annotations

import os
import re

_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/@\s\"']+@")
_HEADER_SECRET = re.compile(r"(?i)(x-goog-api-key\s*[:=]\s*)\S+")
_ENV_SECRET = re.compile(r"(?i)(JULES_API_KEY\s*=\s*)\S+")


def redact_text(value: str) -> str:
    """Remove known credential forms from diagnostics before they leave the process."""

    result = _URL_USERINFO.sub(r"\1[REDACTED]@", value)
    result = _HEADER_SECRET.sub(r"\1[REDACTED]", result)
    result = _ENV_SECRET.sub(r"\1[REDACTED]", result)
    api_key = os.environ.get("JULES_API_KEY", "")
    if api_key:
        result = result.replace(api_key, "[REDACTED]")
    return result
