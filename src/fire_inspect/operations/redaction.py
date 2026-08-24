from __future__ import annotations

import logging
import re
from collections.abc import Iterable

BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+\-/]+=*")
COOKIE = re.compile(r"(?i)\b(sessionid|csrftoken|cookie)\s*[=:]\s*[^\s;,]+")
API_KEY = re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key)\s*[=:]\s*[^\s;,]+")
PHONE = re.compile(r"(?<!\d)1\d{10}(?!\d)")
PRC_ID = re.compile(r"(?<![0-9Xx])\d{17}[0-9Xx](?![0-9Xx])")


def mask_identifier(value: str, *, visible_prefix: int = 2, visible_suffix: int = 2) -> str:
    if len(value) <= visible_prefix + visible_suffix:
        return "*" * len(value)
    return (
        value[:visible_prefix]
        + "*" * (len(value) - visible_prefix - visible_suffix)
        + value[-visible_suffix:]
    )


def redact_text(value: object, *, sensitive_roots: Iterable[str] = ()) -> str:
    text = str(value)
    text = BEARER.sub(r"\1<redacted>", text)
    text = COOKIE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = API_KEY.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = PHONE.sub(
        lambda match: mask_identifier(match.group(0), visible_prefix=3, visible_suffix=2), text
    )
    text = PRC_ID.sub(
        lambda match: mask_identifier(match.group(0), visible_prefix=3, visible_suffix=2), text
    )
    for root in sorted(
        {item.rstrip("/") for item in sensitive_roots if item}, key=len, reverse=True
    ):
        text = text.replace(root, "<storage-root>")
    return text


class SensitiveDataFilter(logging.Filter):
    def __init__(self, *, sensitive_roots: Iterable[str] = ()) -> None:
        super().__init__()
        self.sensitive_roots = tuple(sensitive_roots)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage(), sensitive_roots=self.sensitive_roots)
        record.args = ()
        return True
