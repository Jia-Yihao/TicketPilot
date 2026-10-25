"""PII masking for anything that leaves the request path.

Log lines, trace spans and evaluation samples must not carry the personal
data the user typed. Only the patterns below are masked — business
identifiers such as the order id stay readable, because a trace that cannot
name the order cannot be replayed.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["redact", "redact_payload", "digest"]

# Mainland China mobile numbers, optionally prefixed with +86.
_PHONE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# 18 digit national identity numbers, with or without separators.
_ID_CARD = re.compile(
    r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
# 16 to 19 digit bank card numbers.
_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")

_MASK = "***"


def redact(text: str) -> str:
    """Mask phone numbers, emails, id numbers and card numbers in free text."""
    if not text:
        return text
    masked = _PHONE.sub(_MASK, text)
    masked = _EMAIL.sub(_MASK, masked)
    masked = _ID_CARD.sub(_MASK, masked)
    return _BANK_CARD.sub(_MASK, masked)


def redact_payload(value: Any) -> Any:
    """Recursively mask a payload; structure and types are preserved."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_payload(item) for item in value]
    return value


def digest(value: str, *, length: int = 16) -> str:
    """A stable, non-reversible fingerprint of a value."""
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
