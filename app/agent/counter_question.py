"""The counter-question data carrier: what the one-round clarification asks.

The wording and the labels live in ``counter_question.yaml`` (口径
``.qoder/rules/clarify-counter-question.md`` §4.6) so they can be reviewed
without touching code. This module loads that file, validates it, and gives
the F-1 wiring (a later task) three read-only values:

* ``prompt_template`` — the trunk of the question (§4.5; the handoff tail
  sentence is reused from ``_clarify`` and deliberately *not* stored here),
* ``labels`` — ``intent_id`` to the customer-facing Chinese display name,
* ``max_options`` — how many options may be shown (a data value, §4.3).

Like the intent catalog, unknown keys are refused instead of silently read:
a typo here would change what the customer is asked. Whether ``labels``
covers the intent catalog is *not* checked here — that cross-file coupling
is pinned by ``tests/test_counter_question_catalog.py`` instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "CounterQuestionSpec",
    "load_counter_question",
    "get_counter_question",
]

_CATALOG_PATH = Path(__file__).with_name("counter_question.yaml")

# A field the loader does not know is a typo, and a typo in this file would
# silently change the question the customer sees. Unknown keys are refused.
_TOP_FIELDS = frozenset({"version", "prompt_template", "labels", "max_options"})


@dataclass(frozen=True, slots=True)
class CounterQuestionSpec:
    """The loaded counter-question table."""

    version: str
    prompt_template: str
    labels: dict[str, str]
    max_options: int


def _reject_unknown(item: Any, allowed: frozenset[str], where: str) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"The {where} entry must be a mapping.")
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise ValueError(f"Unknown field(s) in the {where}: {', '.join(unknown)}.")


def load_counter_question(path: Path) -> CounterQuestionSpec:
    """Read and validate one counter-question table.

    Every rule that breaks the contract is an error, not a default: a half
    loaded table would send the customer a broken question.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("The counter-question table must be a mapping.")
    _reject_unknown(raw, _TOP_FIELDS, "counter-question table")
    missing = sorted(_TOP_FIELDS - set(raw))
    if missing:
        raise ValueError(f"Missing field(s) in the counter-question table: {', '.join(missing)}.")

    version = str(raw["version"])
    if not version.strip():
        raise ValueError("The counter-question table needs a non-empty version.")

    prompt_template = raw["prompt_template"]
    if not isinstance(prompt_template, str) or not prompt_template.strip():
        raise ValueError("prompt_template must be a non-empty string.")

    max_options = raw["max_options"]
    if isinstance(max_options, bool) or not isinstance(max_options, int) or max_options <= 0:
        raise ValueError("max_options must be a positive integer.")

    raw_labels = raw["labels"]
    if not isinstance(raw_labels, dict) or not raw_labels:
        raise ValueError("labels must be a non-empty mapping.")
    labels: dict[str, str] = {}
    for key, value in raw_labels.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Every label key must be a non-empty intent id.")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"The label for {key!r} must be a non-empty string.")
        labels[key] = value

    return CounterQuestionSpec(
        version=version,
        prompt_template=prompt_template,
        labels=labels,
        max_options=max_options,
    )


@lru_cache(maxsize=1)
def get_counter_question() -> CounterQuestionSpec:
    """The process-wide counter-question table."""
    return load_counter_question(_CATALOG_PATH)
