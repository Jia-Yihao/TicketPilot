"""Deterministic eval readings, stored in the repository so they can be diffed.

Five offline eval sources each print a table that nobody can compare across
runs. This module runs them, projects every **deterministic** number out of
their JSON output, and stores it as ``evals/baseline/<source>.json``. A later
run re-projects the same keys and compares them: identical readings pass,
anything else is a ``DRIFT`` line and a non-zero exit code.

What belongs in a baseline
    Only readings that are a pure function of the committed data and code:
    pass counts, per-layer metric values, gate thresholds, money and ledger
    counts, handoff rates. The gate is "the same data twice gives the same
    numbers", so a key that moves between two identical runs would make the
    gate useless; those keys are listed in ``VOLATILE`` and never stored.

    Wall-clock readings are excluded on purpose (``durations_ms``,
    ``turn_latency_ms``, ``wall_seconds``, ``outbox.elapsed_ms`` and the
    per-bucket ``rule_latency_ms`` sums): they measure this machine on this
    run, not the data, and a tolerance wide enough to absorb them would also
    swallow a real regression. Hour buckets (``by_hour`` /
    ``abandon_by_hour``) are excluded because they split by the clock hour the
    run happens to land in. ``llm`` is excluded because it is null in rules
    mode and holds live call counters in ``pydantic_ai`` mode — the two are
    not comparable, and the rules path is the one this gate runs.

    This is not a performance baseline. Latency and cost readings belong to
    ``bench/``, a directory this repository has not created yet; putting them
    here would make every run red for reasons that have nothing to do with the
    data.

Usage::

    python evals/metrics_baseline.py --record
    python evals/metrics_baseline.py --check
    python evals/metrics_baseline.py --check --only golden,support
    python evals/metrics_baseline.py --check --tolerance "mrr=0.01"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = Path(__file__).with_name("baseline")
SOURCES: tuple[str, ...] = ("golden", "support", "intent", "retrieval", "business")

# 子进程跑整套评测，默认 30 分钟够用；超时即报错，不静默放行。
TIMEOUT_SECONDS = 1800

# 易变路径（前缀匹配）：这些读数描述「这次跑的机器」，不描述「这批数据」。
VOLATILE: tuple[str, ...] = (
    "durations_ms",
    "turn_latency_ms",
    "wall_seconds",
    "outbox.elapsed_ms",
    "llm",
    "handoff_metrics.by_hour",
    "handoff_metrics.abandon_by_hour",
    "security_metrics.by_hour",
)

# 段名级排除：同一类易变读数会出现在多个前缀下面（各层的耗时、各来源的小时桶），
# 逐个写死前缀既啰嗦又容易漏一行，所以按段名兜住。
VOLATILE_SEGMENTS: tuple[str, ...] = ("by_hour", "abandon_by_hour", "rule_latency_ms")

_SCRIPTS: dict[str, str] = {
    "golden": "evals/run.py",
    "support": "evals/support_run.py",
    "intent": "evals/intent_metrics.py",
    "retrieval": "evals/retrieval_metrics.py",
    "business": "evals/business_metrics.py",
}

# 这三个脚本支持 `--json PATH`，走临时文件比混在 PASS 行里更干净；
# retrieval 与 business 的 `--json` 是布尔开关，只能从 stdout 拿。
_FILE_JSON_SOURCES = frozenset({"golden", "support", "intent"})

Reading = int | float | bool


# ------------------------------------------------------------------ projection
def flatten(payload: Mapping[str, Any], prefix: str = "") -> dict[str, Reading]:
    """Collapse a nested report into ``"a.b.c" -> number | bool``.

    Strings, ``None`` and lists are dropped: a description, a missing value and
    a per-case row are not readings, and keeping them would make the baseline
    a copy of the report instead of a gate.
    """
    readings: dict[str, Reading] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            readings.update(flatten(value, path))
        elif isinstance(value, bool):
            readings[path] = value
        elif isinstance(value, (int, float)):
            readings[path] = value
    return readings


def is_volatile(key: str) -> bool:
    """True when the key describes the run instead of the data."""
    if any(segment in VOLATILE_SEGMENTS for segment in key.split(".")):
        return True
    return any(key == prefix or key.startswith(prefix + ".") for prefix in VOLATILE)


def _keep(readings: Mapping[str, Reading]) -> dict[str, Reading]:
    return {key: value for key, value in readings.items() if not is_volatile(key)}


def _project_run_summary(report: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "passed": int(report["passed"]),
        "total": int(report["total"]),
        "failed_count": len(report.get("failed") or []),
    }


def _project_intent(report: Mapping[str, Any]) -> Mapping[str, Any]:
    invariants = report.get("invariants") or {}
    return {
        "layers": report.get("layers") or {},
        "cue_overlap": report.get("cue_overlap") or {},
        "invariants": {
            "ok": bool(invariants.get("ok")),
            "failure_count": len(invariants.get("failures") or []),
        },
    }


def _project_retrieval(report: Mapping[str, Any]) -> Mapping[str, Any]:
    channels: dict[str, Any] = {}
    for item in report.get("channels") or []:
        channels[str(item["channel"])] = {
            "metrics": item.get("metrics") or {},
            "layers": item.get("layers") or {},
            "dense_decisions": item.get("dense_decisions") or {},
            "failure_count": len(item.get("failures") or []),
        }
    return {"channels": channels, "thresholds": report.get("thresholds") or {}}


def readings(source: str, report: Mapping[str, Any]) -> dict[str, Reading]:
    """The committed readings of one source, projected and filtered."""
    if source in ("golden", "support"):
        return _keep(flatten(_project_run_summary(report)))
    if source == "intent":
        return _keep(flatten(_project_intent(report)))
    if source == "retrieval":
        return _keep(flatten(_project_retrieval(report)))
    if source == "business":
        # 业务面全部数值叶子都要，但逐条 case 明细不在 report 顶层，故直接展开。
        return _keep(flatten(report))
    raise ValueError(f"unknown source: {source}")


# ------------------------------------------------------------------ comparison
def _as_readings(payload: Mapping[str, Any]) -> Mapping[str, Reading]:
    stored = payload.get("readings")
    if isinstance(stored, Mapping):
        return stored
    return payload  # 也接受裸的扁平读数，方便直接比对两份报告


def compare(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    tolerances: Mapping[str, float] | None = None,
) -> list[str]:
    """Return one description per violated reading; empty list means no drift."""
    limits = tolerances or {}
    base = _as_readings(baseline)
    cur = _as_readings(current)
    problems: list[str] = []

    for key in sorted(set(base) | set(cur)):
        if key not in cur:
            problems.append(f"{key}: baseline={base[key]} current=<missing>")
            continue
        if key not in base:
            problems.append(f"{key}: baseline=<missing> current={cur[key]}")
            continue
        left, right = base[key], cur[key]
        if isinstance(left, bool) or isinstance(right, bool):
            # 布尔读数没有容差：True 与 1.0 不是同一个结论。
            if not (isinstance(left, bool) and isinstance(right, bool) and left == right):
                problems.append(f"{key}: baseline={left} current={right}")
            continue
        limit = float(limits.get(key, 0.0))
        if abs(float(right) - float(left)) > limit:
            problems.append(f"{key}: baseline={left} current={right}")
    return problems


# ------------------------------------------------------------------- execution
def _recorded_command(source: str) -> list[str]:
    script = _SCRIPTS[source]
    if source in _FILE_JSON_SOURCES:
        # 临时路径每次都不同，记一个可复制的占位符比记一个已经消失的路径有用。
        return [script, "--json", "<tmp>"]
    return [script, "--json"]


def _run_source(source: str) -> dict[str, Any]:
    """Run one eval script and parse the JSON it produced."""
    script = _SCRIPTS[source]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with tempfile.TemporaryDirectory() as directory:
        argv = [sys.executable, script]
        target: Path | None = None
        if source in _FILE_JSON_SOURCES:
            target = Path(directory) / f"{source}.json"
            argv += ["--json", str(target)]
        else:
            argv.append("--json")
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            tail = "\n".join((completed.stderr or "").strip().splitlines()[-20:])
            raise RuntimeError(
                f"{script} --json exited {completed.returncode}\n{tail}".rstrip()
            )
        text = (
            target.read_text(encoding="utf-8") if target is not None else completed.stdout
        )
    return json.loads(text)


def _payload(source: str, values: Mapping[str, Reading]) -> dict[str, Any]:
    return {
        "_meta": {
            "source": source,
            "command": _recorded_command(source),
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        },
        "readings": dict(sorted(values.items())),
    }


def _dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _selected(only: str | None) -> tuple[str, ...]:
    if not only:
        return SOURCES
    wanted = tuple(part.strip() for part in only.split(",") if part.strip())
    unknown = [name for name in wanted if name not in SOURCES]
    if unknown or not wanted:
        raise SystemExit(
            f"unknown source(s): {unknown or list(wanted)}; choose from {list(SOURCES)}"
        )
    return wanted


def _parse_tolerances(text: str | None) -> dict[str, float]:
    tolerances: dict[str, float] = {}
    if not text:
        return tolerances
    for part in text.split(","):
        entry = part.strip()
        if not entry:
            continue
        key, separator, value = entry.partition("=")
        if not separator or not key.strip():
            raise SystemExit(f"bad tolerance entry: {entry!r} (expected key=value)")
        tolerances[key.strip()] = float(value)
    return tolerances


def _tolerances_for(source: str, tolerances: Mapping[str, float]) -> dict[str, float]:
    """Accept both ``key`` and ``<source>.key``; the source-scoped form wins."""
    scoped: dict[str, float] = {}
    for key, value in tolerances.items():
        prefix = f"{source}."
        if key.startswith(prefix):
            scoped[key[len(prefix) :]] = value
        else:
            scoped.setdefault(key, value)
    return scoped


def _record(target: Path, sources: Sequence[str]) -> int:
    target.mkdir(parents=True, exist_ok=True)
    for source in sources:
        values = readings(source, _run_source(source))
        path = target / f"{source}.json"
        path.write_text(_dump(_payload(source, values)), encoding="utf-8", newline="\n")
        print(f"recorded {source}: {len(values)} readings -> {path.as_posix()}")
    return 0


def _check(target: Path, sources: Sequence[str], tolerances: Mapping[str, float]) -> int:
    drift = 0
    total = 0
    for source in sources:
        path = target / f"{source}.json"
        if not path.is_file():
            print(f"DRIFT {source}.<baseline>: baseline=<missing> current={path.as_posix()}")
            drift += 1
            continue
        baseline = json.loads(path.read_text(encoding="utf-8"))
        total += len(baseline.get("readings") or {})
        values = readings(source, _run_source(source))
        problems = compare(baseline, values, _tolerances_for(source, tolerances))
        for problem in problems:
            print(f"DRIFT {source}.{problem}")
        drift += len(problems)
    if drift:
        print(f"BASELINE DRIFT: {drift} reading(s)")
        return 1
    print(f"BASELINE OK ({total} readings across {len(sources)} sources)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record or check the deterministic eval readings."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record", action="store_true", help="Write the baseline files.")
    mode.add_argument(
        "--check", action="store_true", help="Compare the current readings with the baseline."
    )
    parser.add_argument(
        "--only",
        help=f"Comma separated sources, default all: {', '.join(SOURCES)}.",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=BASELINE_DIR,
        help="Directory holding the baseline JSON files (default: evals/baseline).",
    )
    parser.add_argument(
        "--tolerance",
        help='Per-reading tolerance, e.g. "mrr=0.01,retrieval.channels.bm25.metrics.mrr=0.02".',
    )
    args = parser.parse_args(argv)

    if not args.record and not args.check:
        parser.print_help()
        return 2

    sources = _selected(args.only)
    tolerances = _parse_tolerances(args.tolerance)
    if args.record:
        return _record(args.dir, sources)
    return _check(args.dir, sources, tolerances)


if __name__ == "__main__":
    sys.exit(main())
