"""What the candidate kernel would have offered the model, measured offline (P8).

The shipped claim for P8 is narrow: *before the model is asked, the server can
name the catalog rows it has evidence for, and it can say how strong that
evidence is.* This harness measures that claim against the held-out intent
corpus — 346 sentences in two layers, human-labelled — by running the same
deterministic kernel the chat entry uses (``app/agent/candidates.py``) and
reporting what it produced.

The corpus pass is offline and deterministic: no model, no network, no clock, no
database, and two runs print the same bytes. What the model *does* with a
shortlist — how often it names a row outside it, how often it answers ``NONE``,
whether the constraint changes the final route — cannot come from a corpus, so
without ``--with-model`` the report marks that block 未测 instead of inferring
it. Reading the coverage number as "the model would have been right that often"
is the mistake the notes are written to prevent.

Two denominators, because one would lie. When no row gets content evidence the
kernel does not narrow (``narrowed=False``) and the model sees the whole
catalog; counting those sentences as covered would credit the shortlist with a
hit it never made. So:

* ``narrowed.coverage`` is ``gold in candidates`` over narrowed sets only;
* ``no_signal.share`` is the rate at which the rules and the cue table are blind
  together — the gap-filling rate, and the most useful number here;
* ``no_signal.avg_candidates`` equals the catalog size, which is exactly why
  those sets are not shortlists.

Run it with::

    python evals/intent_candidates.py
    python evals/intent_candidates.py --json
    python evals/intent_candidates.py --max-candidates 2

    OPENAI_BASE_URL=http://127.0.0.1:1234/v1 OPENAI_API_KEY=lmstudio \\
      python evals/intent_candidates.py --with-model --model-limit 12
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.candidates import Bucket, CandidateSet, build_candidates
from app.agent.intent_catalog import IntentCatalog, get_catalog
from app.agent.routing import RoutePlan, classify, extract_amount, extract_order_id
from app.models.enums import Intent

DATASET_PATH = Path(__file__).with_name("intent") / "intent_set.json"
CORPUS_FILE = "evals/intent/intent_set.json"

REPORT_VERSION = "2026-10-26"

# The keys are part of the contract: a test pins the shape so a rewritten
# report cannot quietly drop the denominator it was meant to split.
TOP_KEYS = frozenset({"version", "corpus", "config", "layers", "model", "notes"})
LAYER_KEYS = frozenset(
    {
        "cases",
        "narrowed",
        "no_signal",
        "buckets",
        "gold_bucket",
        "unconditional",
        "per_intent",
    }
)
BUCKET_LABELS: tuple[Bucket, ...] = ("high", "medium", "low")

# The kernel treats a negative cap as "do not truncate"; the harness uses a
# second uncapped pass to tell whether the shipped cap dropped a row.
_NO_CAP = -1

DEFAULT_MAX_CANDIDATES = 6
DEFAULT_MODEL = "openai:qwen/qwen3-8b"
# A local endpoint costs seconds per call, so the A/B is a bounded sample by
# default and the report names both the sample and its denominator.
DEFAULT_MODEL_LIMIT = 24

UNTESTED_NOTE = (
    "未测 (untested): the model-side numbers — how often it names a row outside "
    "the candidates, and how often it answers NONE — cannot come from an offline "
    "corpus. Re-run with ``--with-model`` (or ``scripts/llm_gap_probe.py``) "
    "against a real endpoint; until then this report describes only what the "
    "server would have handed over."
)

TAIL_NOTES = (
    "Coverage has two denominators. ``narrowed.coverage`` is gold-in-candidates "
    "over narrowed sets only; a no-signal set offers the whole catalog, so "
    "counting it as covered would credit the shortlist with hits it never made.",
    "``no_signal.share`` is the rate at which the rules and the cue table are "
    "blind together. It is the gap-filling rate, not a candidate-set failure: "
    "the kernel is designed not to narrow there.",
    "``buckets`` and ``gold_bucket`` count narrowed sets only, and they carry no "
    "verdict: no threshold is applied to a bucket distribution, because the "
    "calibration data that would justify one does not exist yet.",
    f"``context:`` and ``order:`` evidence is not exercised: {CORPUS_FILE} is "
    "single-sentence with no previous turn and no order lookup, so every reading "
    "here is a first turn over an empty conversation.",
    "This is a held-out corpus, not production traffic. The layers (``external`` "
    "real phrasings, ``authored_gap`` written coverage) are reported apart and "
    "never summed into one accuracy.",
)


@dataclass(frozen=True, slots=True)
class CaseReading:
    """One sentence as the candidate kernel saw it."""

    case_id: str
    layer: str
    gold: str
    narrowed: bool
    size: int
    covered: bool
    gold_bucket: str
    cap_hit: bool
    buckets: Mapping[Bucket, int]
    offers_handoff: bool
    offers_unknown: bool
    # The model is only allowed to decide the intent where the rules gave up.
    is_gap: bool
    candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GapRequest:
    """One rule-blind sentence handed to a model runner.

    The runner is expected to call the shipped ``IntentBrain`` twice — once with
    candidates on, once off — so the harness measures the production path rather
    than a re-implementation of it.
    """

    case_id: str
    layer: str
    gold: str
    text: str
    context: Mapping[str, Any]
    route: RoutePlan
    narrowed: bool
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GapAnswer:
    """What the two brains made of one sentence."""

    case_id: str
    narrowed: bool
    on_route: str
    on_reason: str | None
    on_source: str
    on_requests: int
    off_route: str
    off_requests: int


Runner = Callable[[GapRequest], GapAnswer]


@dataclass(frozen=True, slots=True)
class _ModelPass:
    answers: tuple[GapAnswer, ...]
    gap_cases: int
    sampled: int


def load_dataset(path: Path | None = None) -> dict[str, Any]:
    """Read the corpus as data; the harness never builds its own labels."""
    return json.loads(Path(path or DATASET_PATH).read_text(encoding="utf-8"))


def _slot_context(text: str) -> dict[str, Any]:
    """The context a first chat turn would carry for this message.

    Same rule as the rest of the evals: slots come from the sentence itself,
    because this corpus has no previous turn to inherit an intent from.
    """
    context: dict[str, Any] = {}
    order_id = extract_order_id(text)
    amount_cents, _invalid = extract_amount(text)
    if order_id:
        context["order_id"] = order_id
    if amount_cents is not None:
        context["amount_cents"] = amount_cents
    return context


def _cases(dataset: Mapping[str, Any], catalog: IntentCatalog) -> list[dict[str, Any]]:
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("No cases in the intent corpus; refusing an empty report.")
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("id", ""))
        if not case_id or case_id in seen:
            raise ValueError(f"Case id missing or duplicated: {case_id!r}")
        seen.add(case_id)
        if not str(case.get("text", "")).strip():
            raise ValueError(f"{case_id}: empty text.")
        if not str(case.get("layer", "")).strip():
            raise ValueError(f"{case_id}: a case needs a layer.")
        gold = str(case.get("gold", ""))
        if gold not in catalog.specs:
            raise ValueError(f"{case_id}: gold {gold!r} is not a catalog row.")
    return [dict(case) for case in cases]


def _bucket_counts(candidate_set: CandidateSet) -> dict[Bucket, int]:
    counts: dict[Bucket, int] = {label: 0 for label in BUCKET_LABELS}
    for candidate in candidate_set.candidates:
        counts[candidate.bucket] += 1
    return counts


def _read_case(
    case: dict[str, Any], *, catalog: IntentCatalog, max_candidates: int
) -> tuple[CaseReading, GapRequest]:
    text = str(case["text"])
    gold = str(case["gold"])
    context = _slot_context(text)
    route = classify(text, context)
    offered = build_candidates(
        text, context, route, catalog=catalog, max_candidates=max_candidates
    )
    uncapped = build_candidates(
        text, context, route, catalog=catalog, max_candidates=_NO_CAP
    )
    ids = offered.ids()
    reading = CaseReading(
        case_id=str(case["id"]),
        layer=str(case["layer"]),
        gold=gold,
        narrowed=offered.narrowed,
        size=len(offered.candidates),
        covered=offered.allows(gold),
        gold_bucket=offered.bucket_of(gold) or "absent",
        cap_hit=len(offered.candidates) < len(uncapped.candidates),
        buckets=_bucket_counts(offered) if offered.narrowed else {},
        offers_handoff="HUMAN_HANDOFF" in ids,
        offers_unknown="UNKNOWN" in ids,
        is_gap=route.intent is Intent.UNKNOWN,
        candidate_ids=ids,
    )
    request = GapRequest(
        case_id=reading.case_id,
        layer=reading.layer,
        gold=gold,
        text=text,
        context=context,
        route=route,
        narrowed=offered.narrowed,
        candidate_ids=ids,
    )
    return reading, request


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _average(values: Sequence[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _layer_report(name: str, readings: Sequence[CaseReading]) -> dict[str, Any]:
    narrowed = [case for case in readings if case.narrowed]
    blind = [case for case in readings if not case.narrowed]

    buckets: dict[Bucket, int] = {label: 0 for label in BUCKET_LABELS}
    for case in narrowed:
        # Rows the model would have seen, not sentences: a shortlist with four
        # high rows and one low row is not one "high case".
        for label, count in case.buckets.items():
            buckets[label] += count

    per_intent: dict[str, dict[str, int]] = {}
    for case in readings:
        row = per_intent.setdefault(
            case.gold, {"cases": 0, "narrowed": 0, "covered": 0}
        )
        row["cases"] += 1
        if case.narrowed:
            row["narrowed"] += 1
            if case.covered:
                row["covered"] += 1

    return {
        "cases": len(readings),
        "narrowed": {
            "sets": len(narrowed),
            "share": _rate(len(narrowed), len(readings)),
            "coverage": _rate(sum(1 for case in narrowed if case.covered), len(narrowed)),
            "avg_candidates": _average([case.size for case in narrowed]),
            "cap_hits": sum(1 for case in narrowed if case.cap_hit),
        },
        "no_signal": {
            "sets": len(blind),
            "share": _rate(len(blind), len(readings)),
            "avg_candidates": _average([case.size for case in blind]),
        },
        "buckets": dict(buckets),
        "gold_bucket": {
            label: sum(1 for case in narrowed if case.gold_bucket == label)
            for label in (*BUCKET_LABELS, "absent")
        },
        "unconditional": {
            "human_handoff": _rate(
                sum(1 for case in narrowed if case.offers_handoff), len(narrowed)
            ),
            "unknown": _rate(sum(1 for case in narrowed if case.offers_unknown), len(narrowed)),
        },
        "per_intent": {gold: per_intent[gold] for gold in sorted(per_intent)},
    }


def _spread(requests: Sequence[GapRequest], limit: int) -> list[GapRequest]:
    """Take the sample round-robin across layers, corpus order inside a layer.

    The corpus is grouped by layer, so "the first N sentences" would sample one
    layer and report a rate for both. Round-robin is still deterministic and
    still reproducible from the command line.
    """
    if limit < 0:
        return list(requests)
    by_layer: dict[str, list[GapRequest]] = {}
    for request in requests:
        by_layer.setdefault(request.layer, []).append(request)
    # First-appearance order, not alphabetical: the sample should start where the
    # corpus starts.
    queues = [list(group) for group in by_layer.values()]
    chosen: list[GapRequest] = []
    while queues and len(chosen) < limit:
        for queue in list(queues):
            chosen.append(queue.pop(0))
            if not queue:
                queues.remove(queue)
            if len(chosen) >= limit:
                break
    return chosen


def _ask(
    runner: Runner,
    requests: Sequence[GapRequest],
    *,
    limit: int,
) -> _ModelPass:
    """Send at most ``limit`` rule-blind sentences to the model.

    The budget is split across the two kinds of set, narrowed first: an answer
    can only fall outside a shortlist that is shorter than the catalog, so the
    narrowed pool is the only place an out-of-candidates reading can show up,
    while the whole-catalog pool still carries the ``NONE`` rate and the
    model's own jitter. Selection is computed from the data, never drawn at
    random, so the sample is reproducible from the command line.

    ``limit`` bounds the spend, not the denominator: the report keeps the full
    ``gap_cases`` count next to ``sampled`` so nobody reads a sample as a rate
    over the corpus.
    """
    binds = [request for request in requests if request.narrowed]
    blind = [request for request in requests if not request.narrowed]
    if limit < 0:
        chosen = list(requests)
    else:
        chosen = _spread(binds, (limit + 1) // 2)
        chosen.extend(_spread(blind, limit - len(chosen)))
        if len(chosen) < limit:
            remaining = [request for request in binds if request not in chosen]
            chosen.extend(_spread(remaining, limit - len(chosen)))
    return _ModelPass(
        answers=tuple(runner(request) for request in chosen),
        gap_cases=len(requests),
        sampled=len(chosen),
    )


def _model_block(
    pass_: _ModelPass | None,
    *,
    readings: Sequence[CaseReading],
    gold_by_id: Mapping[str, str],
    layer_by_id: Mapping[str, str],
    candidates_by_id: Mapping[str, tuple[str, ...]],
    narrowed_by_id: Mapping[str, bool],
    model_name: str,
    limit: int,
) -> dict[str, Any]:
    gap_cases = sum(1 for case in readings if case.is_gap)
    if pass_ is None:
        return {
            "status": "untested",
            "reason": "offline corpus; pass --with-model to ask a real endpoint",
            "gap_cases": gap_cases,
            "sampled": 0,
            "cases": [],
        }

    answers = pass_.answers
    out_of = [answer for answer in answers if answer.on_reason == "model_out_of_candidates"]
    none = [answer for answer in answers if answer.on_reason == "model_none"]
    changed = [answer for answer in answers if answer.on_route != answer.off_route]
    asked = [answer.case_id for answer in answers]
    return {
        "status": "measured",
        "model": model_name,
        "gap_cases": pass_.gap_cases,
        "sampled": pass_.sampled,
        "limit": limit,
        "out_of_candidates": {"cases": len(out_of), "share": _rate(len(out_of), len(answers))},
        "none": {"cases": len(none), "share": _rate(len(none), len(answers))},
        "route_changed": {"cases": len(changed), "share": _rate(len(changed), len(answers))},
        "gold_agreement_on": _rate(
            sum(1 for answer in answers if answer.on_route == gold_by_id[answer.case_id]),
            len(answers),
        ),
        "gold_agreement_off": _rate(
            sum(1 for answer in answers if answer.off_route == gold_by_id[answer.case_id]),
            len(answers),
        ),
        "avg_requests_on": _average([answer.on_requests for answer in answers]),
        "avg_requests_off": _average([answer.off_requests for answer in answers]),
        "cases": [
            {
                "id": answer.case_id,
                "layer": layer_by_id[answer.case_id],
                "gold": gold_by_id[answer.case_id],
                "narrowed": narrowed_by_id[answer.case_id],
                "candidates": list(candidates_by_id[answer.case_id]),
                "on_route": answer.on_route,
                "on_reason": answer.on_reason,
                "off_route": answer.off_route,
                "changed": answer.on_route != answer.off_route,
            }
            for answer in answers
        ],
        "asked": asked,
    }


def _notes(model: Mapping[str, Any]) -> list[str]:
    if model["status"] == "untested":
        return [UNTESTED_NOTE, *TAIL_NOTES]
    return [
        (
            f"Model A/B: {model['sampled']} of {model['gap_cases']} rule-blind "
            f"sentences ({model['model']}), budget split between the narrowed "
            "sets first and the whole-catalog sets, round-robin across layers — "
            "a sample, so the rates are directional and their denominator is the "
            "sample, not the corpus. The same sentences went through "
            "``IntentBrain`` with candidates on and off; the shipped adoption "
            "rule was not re-implemented here. A local endpoint is not "
            "deterministic, so a route difference between the two passes is not "
            "by itself evidence that the constraint changed the reading."
        ),
        *TAIL_NOTES,
    ]


def evaluate(
    dataset: Mapping[str, Any],
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    catalog: IntentCatalog | None = None,
    runner: Runner | None = None,
    model_name: str = "",
    model_limit: int = DEFAULT_MODEL_LIMIT,
) -> dict[str, Any]:
    """Score the whole corpus through the kernel and shape the report.

    ``max_candidates`` is the shipped cap; pass ``-1`` to see what the kernel
    offers without truncation. ``runner``, when given, is called once per
    rule-blind sentence and must return what the shipped ``IntentBrain`` did with
    it — that is the only way the model block stops being 未测. No number here is
    a pass/fail: the report is evidence, and the thresholds that would turn it
    into a gate need the calibration data that does not exist yet.
    """
    active_catalog = catalog or get_catalog()
    cases = _cases(dataset, active_catalog)
    read = [
        _read_case(case, catalog=active_catalog, max_candidates=max_candidates)
        for case in cases
    ]
    readings = [item[0] for item in read]
    requests = {reading.case_id: request for reading, request in read}
    layers = sorted({case.layer for case in readings})

    pass_ = (
        None
        if runner is None
        else _ask(runner, [requests[c.case_id] for c in readings if c.is_gap], limit=model_limit)
    )
    model = _model_block(
        pass_,
        readings=readings,
        gold_by_id={case.case_id: case.gold for case in readings},
        layer_by_id={case.case_id: case.layer for case in readings},
        candidates_by_id={case.case_id: case.candidate_ids for case in readings},
        narrowed_by_id={case.case_id: case.narrowed for case in readings},
        model_name=model_name or "model",
        limit=model_limit,
    )
    return {
        "version": REPORT_VERSION,
        "corpus": {
            "file": CORPUS_FILE,
            "cases": len(readings),
            "catalog_rows": len(active_catalog.specs),
            "layers": {
                name: sum(1 for case in readings if case.layer == name)
                for name in layers
            },
        },
        "config": {
            "max_candidates": max_candidates,
            "kernel": "app/agent/candidates.py",
            "context": "empty (first turn)",
            "evidence_admits_row": ["rule", "cue", "context"],
            "evidence_boosts_row": ["slot", "order"],
        },
        "layers": {
            name: _layer_report(
                name, [case for case in readings if case.layer == name]
            )
            for name in layers
        },
        "model": model,
        "notes": _notes(model),
    }


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4g}"


def format_report(report: Mapping[str, Any]) -> str:
    """Print the layers apart; a merged number is the one thing not to show."""
    corpus = report["corpus"]
    model = report["model"]
    lines = [
        "TicketPilot intent candidate shortlisting — offline evidence (P8)",
        "",
        f"corpus   : {corpus['cases']} cases over {corpus['catalog_rows']} catalog"
        f" rows  ({corpus['file']})",
        f"config   : max_candidates={report['config']['max_candidates']}"
        f"  context={report['config']['context']}",
    ]
    if model["status"] == "untested":
        lines.append(
            "model    : 未测 — the out-of-candidates / NONE rates need a real "
            "endpoint (--with-model)."
        )
    else:
        lines.extend(
            [
                f"model a/b: {model['model']}  {model['sampled']} of {model['gap_cases']}"
                " rule-blind sentences (narrowed sets first, round-robin by layer)",
                f"           out_of_candidates={model['out_of_candidates']['cases']}"
                f" ({_fmt(model['out_of_candidates']['share'])})"
                f"  none={model['none']['cases']} ({_fmt(model['none']['share'])})",
                f"           route changed={model['route_changed']['cases']}"
                f" ({_fmt(model['route_changed']['share'])})"
                f"  agreement on/off={_fmt(model['gold_agreement_on'])}"
                f"/{_fmt(model['gold_agreement_off'])}",
                f"           avg requests on/off={_fmt(model['avg_requests_on'])}"
                f"/{_fmt(model['avg_requests_off'])}",
            ]
        )
    lines.append("")
    for name, layer in report["layers"].items():
        narrowed = layer["narrowed"]
        blind = layer["no_signal"]
        lines.extend(
            [
                f"layer {name}  ({layer['cases']} cases)",
                f"  narrowed sets : {narrowed['sets']} ({_fmt(narrowed['share'])})"
                f"  coverage={_fmt(narrowed['coverage'])}"
                f"  avg={_fmt(narrowed['avg_candidates'])}"
                f"  capped={narrowed['cap_hits']}",
                f"  no-signal sets: {blind['sets']} ({_fmt(blind['share'])})"
                f"  avg={_fmt(blind['avg_candidates'])} (= whole catalog)",
                "  buckets (rows in narrowed sets): "
                + "  ".join(f"{label}={layer['buckets'][label]}" for label in BUCKET_LABELS),
                "  gold bucket    : "
                + "  ".join(
                    f"{label}={layer['gold_bucket'][label]}"
                    for label in (*BUCKET_LABELS, "absent")
                ),
                "  fallback rows  : "
                f"HUMAN_HANDOFF={_fmt(layer['unconditional']['human_handoff'])}"
                f"  UNKNOWN={_fmt(layer['unconditional']['unknown'])}",
                "  per gold intent (covered/narrowed of cases):",
            ]
        )
        for gold, row in layer["per_intent"].items():
            lines.append(
                f"    {gold:<20} {row['covered']:>3}/{row['narrowed']:>3} narrowed,"
                f" {row['cases']:>3} cases"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def live_runner(model_name: str, *, max_candidates: int) -> Runner:
    """Ask the shipped ``IntentBrain`` twice per sentence: candidates on, then off.

    Nothing here re-implements adoption: both passes go through ``plan()``, so a
    dropped reading is the production rule's doing, not the harness's. One agent
    is shared, so the model configuration (and its usage counters) is identical
    across the two paths.
    """
    from app.agent.pydantic_agent import IntentAgent, IntentBrain

    agent = IntentAgent(model_name=model_name)
    on = IntentBrain(
        mode="pydantic_ai",
        agent=agent,
        candidates_enabled=True,
        max_candidates=max_candidates,
    )
    off = IntentBrain(
        mode="pydantic_ai", agent=agent, candidates_enabled=False
    )

    def run(request: GapRequest) -> GapAnswer:
        context = dict(request.context)
        plan_on, source_on, trace_on = on.plan(request.text, context)
        plan_off, _source_off, trace_off = off.plan(request.text, context)
        return GapAnswer(
            case_id=request.case_id,
            narrowed=request.narrowed,
            on_route=plan_on.intent.value,
            on_reason=trace_on.reason if trace_on is not None else None,
            on_source=source_on,
            on_requests=trace_on.requests if trace_on is not None else 0,
            off_route=plan_off.intent.value,
            off_requests=trace_off.requests if trace_off is not None else 0,
        )

    return run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure the deterministic intent candidate set over the held-out "
            "intent corpus. The corpus pass is offline; --with-model adds the "
            "model-side A/B, and without it those rates stay 未测."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Override the shipped cap; defaults to INTENT_CANDIDATES_MAX.",
    )
    parser.add_argument(
        "--with-model",
        action="store_true",
        help=(
            "Ask a real endpoint on the rule-blind subset, with candidates on and "
            "off. Needs OPENAI_BASE_URL/OPENAI_API_KEY for an OpenAI-compatible "
            "server; each call costs seconds."
        ),
    )
    parser.add_argument("--model-name", default=DEFAULT_MODEL)
    parser.add_argument(
        "--model-limit",
        type=int,
        default=DEFAULT_MODEL_LIMIT,
        help=(
            "How many rule-blind sentences to ask (narrowed sets first, then "
            "round-robin across layers); -1 for all."
        ),
    )
    parser.add_argument(
        "--json",
        nargs="?",
        const="-",
        metavar="PATH",
        help="Write JSON to PATH; with no PATH, print it to stdout.",
    )
    args = parser.parse_args(argv)

    max_candidates = args.max_candidates
    if max_candidates is None:
        from app.core.config import Settings

        max_candidates = Settings().intent_candidates_max

    runner = None
    if args.with_model:
        # The probe's evidence is its table; per-request httpx lines are noise.
        for name in ("httpx", "httpx2"):
            logging.getLogger(name).setLevel(logging.WARNING)
        runner = live_runner(args.model_name, max_candidates=max_candidates)

    report = evaluate(
        load_dataset(args.dataset),
        max_candidates=max_candidates,
        runner=runner,
        model_name=args.model_name if args.with_model else "",
        model_limit=args.model_limit,
    )

    if args.json:
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.json == "-":
            print(payload, end="")
        else:
            path = Path(args.json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8", newline="\n")
            print(f"wrote {path}")
    else:
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(encoding="utf-8")
        print(format_report(report), end="")
        print("notes:")
        for note in report["notes"]:
            print(f"- {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
