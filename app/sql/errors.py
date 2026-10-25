"""Why a statement was refused.

Every refusal carries a stable reason code. The codes are what the
execution ledger stores and what the tests assert on, so a rejection is
explainable to whoever asks why the agent could not answer.
"""

from __future__ import annotations

__all__ = ["SqlRejected", "REASON_CODES"]


# The stable reason codes. A new code means a new rule, not a new wording.
REASON_CODES: tuple[str, ...] = (
    "NOT_SELECT",
    "MULTI_STATEMENT",
    "COMMENT",
    "SYNTAX",
    "UNKNOWN_TABLE",
    "UNKNOWN_COLUMN",
    "UNKNOWN_FUNCTION",
    "STAR_SELECT",
    "SUBQUERY",
    "SET_OPERATION",
    "LIMIT_MISSING",
    "LIMIT_TOO_LARGE",
    "PARAM_NOT_ALLOWED",
    "INLINE_LITERAL_NOT_ALLOWED",
    "LITERAL_TOO_LONG",
    "SCOPE_MISSING",
    "SCOPE_NOT_TOP_LEVEL",
    "NO_PLAN",
)


class SqlRejected(Exception):
    """A statement was refused before it reached the database."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        message = f"{reason}: {detail}" if detail else reason
        super().__init__(message)
