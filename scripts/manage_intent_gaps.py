"""Manage the intent gap queue: ingest, propose, close, ignore, report.

The loop is deliberately boring and local: sampling writes JSONL, ``ingest``
dedups it, a model may only ``propose``, and ``close`` / ``ignore`` require
``--confirm-human`` plus a note. Closing rewrites ``gap_closures.jsonl`` from
the queue, and ``scripts/build_intent_set.py`` merges that file into the same
held-out ``authored_gap`` layer. Nothing here edits the cue table.

Usage::

    python scripts/manage_intent_gaps.py ingest .tmp/bad_cases.jsonl --cycle 2026-10-26
    python scripts/manage_intent_gaps.py propose gap-0123456789ab \\
        --label REFUND_STATUS --note "模型提议" --by model:qwen3-8b --cycle 2026-10-26
    python scripts/manage_intent_gaps.py close gap-0123456789ab \\
        --label REFUND_STATUS --note "问退款进度" --by reviewer \\
        --confirm-human --cycle 2026-10-26
    python scripts/manage_intent_gaps.py report \\
        --before .tmp/intent_before.json --after .tmp/intent_after.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from evals import intent_gaps as gaps
except ModuleNotFoundError:  # editable install maps ``app`` only, not ``evals``
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evals import intent_gaps as gaps


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{number}: record must be an object")
        rows.append(row)
    return rows


def _read_metrics(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load(args: argparse.Namespace, *, missing_ok: bool = False) -> dict[str, Any]:
    if args.queue.exists():
        return gaps.load_queue(args.queue)
    if missing_ok:
        return gaps.new_queue()
    raise FileNotFoundError(f"queue not found: {args.queue}")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_close_pair(
    queue_path: Path,
    queue_text: str,
    closures_path: Path,
    closure_text: str,
) -> None:
    """Write the queue first, then the derived closures, and verify both.

    The queue is the source of truth. Writing it first means a failure while
    writing closures can be rolled back instead of leaving a closure row that
    the queue never recorded. The read-back check catches a short write.
    """
    original = queue_path.read_text(encoding="utf-8") if queue_path.exists() else None
    _write(queue_path, queue_text)
    try:
        _write(closures_path, closure_text)
    except OSError:
        if original is None:
            queue_path.unlink(missing_ok=True)
        else:
            _write(queue_path, original)
        raise
    if queue_path.read_text(encoding="utf-8") != queue_text:
        raise OSError(f"queue read-back mismatch: {queue_path}")
    if closures_path.read_text(encoding="utf-8") != closure_text:
        raise OSError(f"closures read-back mismatch: {closures_path}")


def _ingest(args: argparse.Namespace) -> int:
    records = _read_jsonl(args.path)
    queue, stats = gaps.ingest(
        _load(args, missing_ok=True), records, cycle=args.cycle
    )
    _write(args.queue, gaps.render_queue(queue))
    print(
        f"gap ingest: added={stats['added']} merged={stats['merged']} "
        f"skipped={stats['skipped']} -> {args.queue}"
    )
    return 0


def _propose(args: argparse.Namespace) -> int:
    queue = gaps.propose(
        _load(args),
        args.gap_id,
        label=args.label,
        note=args.note,
        by=args.by,
        cycle=args.cycle,
    )
    _write(args.queue, gaps.render_queue(queue))
    print(f"gap propose: {args.gap_id} still OPEN -> {args.queue}")
    return 0


def _close(args: argparse.Namespace) -> int:
    queue = gaps.close(
        _load(args),
        args.gap_id,
        label=args.label,
        note=args.note,
        by=args.by,
        cycle=args.cycle,
        confirm_human=args.confirm_human,
    )
    rows = gaps.closure_rows(queue)
    _write_close_pair(
        args.queue,
        gaps.render_queue(queue),
        args.closures,
        gaps.render_closures(rows),
    )
    print(
        f"gap close: {args.gap_id} -> {args.closures} "
        f"({len(rows)} closure row(s))"
    )
    return 0


def _ignore(args: argparse.Namespace) -> int:
    queue = gaps.ignore(
        _load(args),
        args.gap_id,
        note=args.note,
        by=args.by,
        cycle=args.cycle,
        confirm_human=args.confirm_human,
    )
    _write(args.queue, gaps.render_queue(queue))
    print(f"gap ignore: {args.gap_id} -> {args.queue}")
    return 0


def _report(args: argparse.Namespace) -> int:
    queue = _load(args, missing_ok=True)
    report = gaps.build_report(
        queue,
        before=_read_metrics(args.before),
        after=_read_metrics(args.after),
        cycle=args.cycle,
    )
    if args.output is not None:
        _write(args.output, gaps.render_report(report))
    counts = report["queue"]
    destination = f" -> {args.output}" if args.output is not None else ""
    print(
        f"gap report: total={counts['total']} OPEN={counts['OPEN']} "
        f"CLOSED={counts['CLOSED']} IGNORED={counts['IGNORED']} "
        f"new_cases={report['new_cases']} "
        f"gate_regression={str(report['gate_regression']).lower()}{destination}"
    )
    return 0


def _common() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--queue", type=Path, default=gaps.QUEUE_PATH)
    parent.add_argument("--closures", type=Path, default=gaps.CLOSURES_PATH)
    return parent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the intent gap loop.")
    sub = parser.add_subparsers(dest="command", required=True)
    common = _common()

    ingest = sub.add_parser("ingest", parents=[common], help="merge sampled JSONL")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--cycle", required=True)
    ingest.set_defaults(func=_ingest)

    propose = sub.add_parser(
        "propose", parents=[common], help="record a model suggestion"
    )
    propose.add_argument("gap_id")
    propose.add_argument("--label", required=True)
    propose.add_argument("--note", required=True)
    propose.add_argument("--by", required=True)
    propose.add_argument("--cycle", required=True)
    propose.set_defaults(func=_propose)

    close = sub.add_parser(
        "close", parents=[common], help="close a gap as human gold"
    )
    close.add_argument("gap_id")
    close.add_argument("--label", required=True)
    close.add_argument("--note", required=True)
    close.add_argument("--by", required=True)
    close.add_argument("--cycle", required=True)
    close.add_argument("--confirm-human", action="store_true")
    close.set_defaults(func=_close)

    ignore = sub.add_parser(
        "ignore", parents=[common], help="mark a gap as correctly unresolved"
    )
    ignore.add_argument("gap_id")
    ignore.add_argument("--note", required=True)
    ignore.add_argument("--by", required=True)
    ignore.add_argument("--cycle", required=True)
    ignore.add_argument("--confirm-human", action="store_true")
    ignore.set_defaults(func=_ignore)

    report = sub.add_parser(
        "report", parents=[common], help="build the cycle report"
    )
    report.add_argument("--before", type=Path)
    report.add_argument("--after", type=Path)
    report.add_argument("--cycle")
    report.add_argument("--output", type=Path)
    report.set_defaults(func=_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"gap loop: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
