"""Guard pinned for `.qoder/rules/benchmark-harness.md` §5.1 (禁区 1) and §7 第 8 条.

Two facts that used to be prose-only are now assertions:

1. Rerank stays opt-in: the declared *class* default in ``Settings`` and the
   shipped environment template both say "off".
2. The offline verdict literal ``OVER BUDGET`` and the documented qualitative
   statement "OVER BUDGET + 默认关闭" (the closed four-file list of
   `.qoder/rules/benchmark-harness.md` §5.1: README.md / docs/下一步规划.md /
   docs/guide/范围与边界.md / docs/文档地图.md) are still there line by line,
   and no line that mentions ``OVER BUDGET`` was rewritten into
   "已优化" / "延迟可接受".

Everything here is read-only: files and declared defaults, no model calls, no
execution of ``evals/rerank_latency.py``. Line numbers are never asserted; the
document checks match strings, because the README worktree drifts.
"""

from __future__ import annotations

import pathlib

from app.core.config import Settings

ROOT = pathlib.Path(__file__).resolve().parents[1]

VERDICT_LITERAL = "OVER BUDGET"
DEFAULT_OFF_PHRASE = "默认关闭"
REWRITE_MARKERS = ("已优化", "延迟可接受")

# The closed list from `.qoder/rules/benchmark-harness.md` §5.1 (禁区 1): every
# line in these four files that names both the verdict and the default-off
# phrase must keep carrying it — a per-line check, not an existence check.
DOC_QUALITATIVE_FILES = (
    "README.md",
    "docs/下一步规划.md",
    "docs/guide/范围与边界.md",
    "docs/文档地图.md",
)

# Pinned per-file co-occurrence baselines (measured 2026-11 with the venv
# interpreter). Without them the per-line assertion would be vacuous: deleting
# a co-occurring line would simply drop it out of the filtered set and the
# guard would stay green.
DOC_CO_OCCURRENCE_BASELINE = {
    "README.md": 2,
    "docs/下一步规划.md": 1,
    "docs/guide/范围与边界.md": 1,
    "docs/文档地图.md": 1,
}


def _lines(relative: str) -> list[str]:
    return (ROOT / relative).read_text(encoding="utf-8").splitlines()


def test_rerank_default_is_off_in_settings() -> None:
    """Assertion 1: the declared *class* default in ``Settings`` keeps rerank opt-in.

    Read ``Settings.model_fields[...].default``, not a ``Settings()`` instance:
    ``app/core/config.py`` carries ``env_file=".env"``, and in pydantic-settings a
    dotenv value outranks the class default, so a local ``.env`` at the repo root
    would make an instance reading stop being the default.
    """
    assert not Settings.model_fields["retrieval_rerank_enabled"].default


def test_env_example_pins_rerank_disabled() -> None:
    """Assertion 2: the env template carries the exact whole line, matched by content."""
    assert "RETRIEVAL_RERANK_ENABLED=false" in _lines(".env.example")


def test_rerank_latency_verdict_literal_still_present() -> None:
    """Assertion 3: the verdict print in the offline judge still says OVER BUDGET."""
    source = (ROOT / "evals" / "rerank_latency.py").read_text(encoding="utf-8")
    assert VERDICT_LITERAL in source


def test_docs_keep_the_over_budget_default_off_qualitative() -> None:
    """Assertion 4: the protected qualitative sentences survive, line by line.

    Positive half (per `.qoder/rules/benchmark-harness.md` §5.1, the closed
    four-file list): every line in those files that names both the verdict and
    the default-off phrase must keep carrying it — a per-line check, not an
    existence check — and each file's co-occurrence count stays pinned to the
    baseline (DOC_CO_OCCURRENCE_BASELINE), so deleting a co-occurring line (or
    its 默认关闭 phrase) turns this red instead of silently shrinking the
    filtered set. Reverse half:
    no line mentioning the verdict carries a rewrite marker — checked over every
    OVER BUDGET line, because replacing "默认关闭" with such a marker would drop
    the line out of the positive match set and a narrower check could never fire.
    """
    for relative in DOC_QUALITATIVE_FILES:
        pinned_count = DOC_CO_OCCURRENCE_BASELINE[relative]
        lines = _lines(relative)
        co_lines = [
            line
            for line in lines
            if VERDICT_LITERAL in line and DEFAULT_OFF_PHRASE in line
        ]
        assert len(co_lines) == pinned_count, (
            f"{relative} carries {len(co_lines)} line(s) naming both "
            f"{VERDICT_LITERAL!r} and {DEFAULT_OFF_PHRASE!r}, expected the "
            f"pinned baseline of {pinned_count}"
        )
        for line in co_lines:
            assert DEFAULT_OFF_PHRASE in line, (
                f"{relative} lost the default-off qualitative on a co-occurring "
                f"line: {line!r}"
            )
        for line in lines:
            if VERDICT_LITERAL not in line:
                continue
            for marker in REWRITE_MARKERS:
                assert marker not in line, (
                    f"{relative} rewrote the verdict line: {line!r}"
                )
