from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    """Naive UTC, so SQLite and PostgreSQL round-trip timestamps identically."""
    return datetime.now(UTC).replace(tzinfo=None)


def utcnow_plus(minutes: int) -> datetime:
    return utcnow() + timedelta(minutes=minutes)
