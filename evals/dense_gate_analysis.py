"""Why the dense channel is gated on BM25's blind spots, not by score alone.

The gate in ``app/retrieval/hybrid.py`` was calibrated on the labelled set, so
the calibration is reproducible instead of a comment:

1. per-case features for every labelled query: BM25 top-1 score, margin to
   top-2, term coverage of the top-1 chunk, BM25 rank of the first relevant
   chunk, and the dense channel's rank for the same chunk;
2. a coverage sweep and a score-only sweep, each reporting recall/MRR/nDCG
   plus how often the embedding endpoint was actually called;
3. the rank-1 misses listed one by one, which is the evidence for choosing
   coverage over a score cutoff.

No model server is required when ``EMBEDDING_BASE_URL`` is unset: the dense
column then uses the offline hashing stand-in and is labelled ``hashing``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import Identity
from app.db.session import build_engine, build_session_factory, create_all
from app.models.document import Document, DocumentChunk
from app.retrieval.dense import EmbeddingProvider, store_embeddings
from app.retrieval.hybrid import DenseGate, HybridPolicySearch
from app.retrieval.search import PolicySearchService
from app.seed import seed_all

try:
    from evals.retrieval_metrics import _dense_provider, _SilencedSparse
except ModuleNotFoundError:  # `python evals/dense_gate_analysis.py`
    from retrieval_metrics import (  # type: ignore[no-redef]
        _dense_provider,
        _SilencedSparse,
    )

DATASET_PATH = Path(__file__).with_name("support_set.json")
DEFAULT_K = (1, 3, 5)
DEFAULT_COVERAGE_THRESHOLDS = (0.25, 0.3, 0.35, 0.4, 0.45, 0.5)
DEFAULT_SCORE_THRESHOLDS = (5.0, 10.0, 13.0, 15.0, 20.0, 26.0)


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    query: str
    identity: Identity
    relevant: frozenset[str]
    bm25_rank: int | None
    dense_rank: int | None
    top1_score: float
    gap: float
    coverage: float


@dataclass(frozen=True, slots=True)
class Reading:
    recall: dict[int, float]
    mrr: float
    ndcg: float
    dense_ran: bool


def _dcg(gains: list[int]) -> float:
    return sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))


def _score(hits: list[str], relevant: frozenset[str], k_values: tuple[int, ...]) -> Reading:
    max_k = max(k_values)
    gains = [1 if chunk_id in relevant else 0 for chunk_id in hits[:max_k]]
    recall = {
        k: sum(gains[:k]) / len(relevant) if relevant else 0.0 for k in k_values
    }
    mrr = next((1.0 / (index + 1) for index, gain in enumerate(gains) if gain), 0.0)
    ideal = [1] * min(len(relevant), max_k)
    ndcg = _dcg(gains) / _dcg(ideal) if ideal else 0.0
    return Reading(recall=recall, mrr=mrr, ndcg=ndcg, dense_ran=False)


def _collect(
    session: Session,
    dataset: dict[str, Any],
    provider: EmbeddingProvider | None,
) -> list[Case]:
    rows = session.execute(
        select(DocumentChunk, Document).join(
            Document, Document.id == DocumentChunk.document_id
        )
    ).all()
    chunk_policy = {
        chunk.id: (document.meta or {}).get("policy_id", "")
        for chunk, document in rows
    }
    chunk_section = {chunk.id: chunk.section for chunk, _ in rows}

    sparse = PolicySearchService(session, max_results=20, min_score=0.01)
    dense_only = HybridPolicySearch(
        session,
        sparse=_SilencedSparse(session, max_results=20, min_score=0.01),
        embedder=provider,
        max_results=20,
    )

    cases: list[Case] = []
    for raw in dataset["cases"]:
        policy_id = raw.get("policy_id")
        if not policy_id:
            continue
        sections = set(raw.get("sections", []))
        relevant = frozenset(
            chunk_id
            for chunk_id, chunk_policy_id in chunk_policy.items()
            if chunk_policy_id == policy_id and chunk_section[chunk_id] in sections
        )
        identity = Identity(
            tenant_id=raw.get("tenant", "demo"),
            user_id=raw.get("user", "alice"),
            roles=frozenset(),
        )
        sparse_hits = sparse.ranked(identity, raw["query"], limit=20)
        bm25_rank = next(
            (index + 1 for index, hit in enumerate(sparse_hits) if hit.chunk.id in relevant),
            None,
        )
        dense_hits = dense_only.search(identity, raw["query"], limit=20)
        dense_rank = next(
            (index + 1 for index, hit in enumerate(dense_hits) if hit.chunk_id in relevant),
            None,
        )
        top1 = sparse_hits[0].score if sparse_hits else 0.0
        gap = top1 - sparse_hits[1].score if len(sparse_hits) > 1 else top1
        coverage = (
            DenseGate.coverage(raw["query"], sparse_hits[0].chunk)
            if sparse_hits
            else 0.0
        )
        cases.append(
            Case(
                case_id=raw["id"],
                query=raw["query"],
                identity=identity,
                relevant=relevant,
                bm25_rank=bm25_rank,
                dense_rank=dense_rank,
                top1_score=top1,
                gap=gap,
                coverage=coverage,
            )
        )
    return cases


def _measure(
    session: Session,
    cases: list[Case],
    provider: EmbeddingProvider | None,
    gate: DenseGate | None,
    *,
    max_k: int,
    settings: Settings,
) -> dict[str, Any]:
    search = HybridPolicySearch(
        session,
        sparse=PolicySearchService(session, max_results=max_k, min_score=0.01),
        embedder=provider,
        rrf_k=settings.retrieval_rrf_k,
        max_results=max_k,
        candidate_pool=settings.retrieval_candidate_pool,
        dense_min_score=settings.retrieval_dense_min_score,
        dense_gate=gate,
    )
    readings: list[Reading] = []
    triggered = 0
    for case in cases:
        hits = search.search(case.identity, case.query, limit=max_k)
        reading = _score([hit.chunk_id for hit in hits], case.relevant, DEFAULT_K)
        ran = search.last_dense_decision not in {"sparse_confident", "no_embedder"}
        readings.append(
            Reading(
                recall=reading.recall,
                mrr=reading.mrr,
                ndcg=reading.ndcg,
                dense_ran=ran,
            )
        )
        triggered += int(ran)
    count = len(readings)
    return {
        "recall@1": sum(item.recall[1] for item in readings) / count,
        "recall@3": sum(item.recall[3] for item in readings) / count,
        "recall@5": sum(item.recall[5] for item in readings) / count,
        "mrr": sum(item.mrr for item in readings) / count,
        "ndcg@5": sum(item.ndcg for item in readings) / count,
        "dense_calls": triggered,
        "cases": count,
    }


def _misses(cases: list[Case]) -> list[Case]:
    """Cases where the first relevant chunk is not the BM25 top-1."""
    return [case for case in cases if case.bm25_rank != 1]


def _format_features(
    cases: list[Case], *, coverage_threshold: float, top1_threshold: float
) -> str:
    lines = [
        "case                     bm25@1  top1   gap   cover  dense@1  decision",
        "-" * 78,
    ]
    gate = DenseGate(min_top1_score=top1_threshold, min_coverage=coverage_threshold)
    for case in cases:
        if case.bm25_rank is None:
            decision = "no_sparse_hits"
        elif case.top1_score < gate.min_top1_score:
            decision = "weak_sparse"
        elif case.coverage <= gate.min_coverage:
            decision = "low_coverage"
        else:
            decision = "skip"
        lines.append(
            f"{case.case_id:<24} {str(case.bm25_rank or '-'):>6} "
            f"{case.top1_score:>6.2f} {case.gap:>5.2f} {case.coverage:>6.2f} "
            f"{str(case.dense_rank or '-'):>8}  {decision}"
        )
    return "\n".join(lines)


def _format_misses(misses: list[Case]) -> str:
    lines = [
        f"BM25 rank-1 misses ({len(misses)} of the labelled cases):",
        "case                     top1   gap   cover  dense@1  what dense sees",
        "-" * 82,
    ]
    for case in misses:
        dense = f"rank {case.dense_rank}" if case.dense_rank else "no hit"
        lines.append(
            f"{case.case_id:<24} {case.top1_score:>6.2f} {case.gap:>5.2f} "
            f"{case.coverage:>6.2f} {str(case.dense_rank or '-'):>7}  {dense}"
        )
    return "\n".join(lines)


def _format_sweep(
    rows: list[dict[str, Any]],
    *,
    label: str,
    caught: list[int],
    total_misses: int,
) -> str:
    lines = [
        f"{label}",
        "threshold  recall@1  recall@3  recall@5   mrr  ndcg@5  dense_calls  misses_caught",
        "-" * 88,
    ]
    for row, hit in zip(rows, caught, strict=True):
        lines.append(
            f"{row['threshold']:<10.2f} "
            f"{row['metrics']['recall@1']:<10.3f} {row['metrics']['recall@3']:<10.3f} "
            f"{row['metrics']['recall@5']:<10.3f} {row['metrics']['mrr']:<5.3f} "
            f"{row['metrics']['ndcg@5']:<7.3f} "
            f"{row['metrics']['dense_calls']:>2}/{row['metrics']['cases']:<7} "
            f"{hit}/{total_misses}"
        )
    return "\n".join(lines)


def _caught(misses: list[Case], row: dict[str, Any]) -> int:
    threshold = row["threshold"]
    if row["kind"] == "coverage":
        return sum(1 for case in misses if case.coverage <= threshold)
    return sum(1 for case in misses if case.top1_score < threshold)


def _run(
    dataset: dict[str, Any],
    database_url: str,
    *,
    coverage_thresholds: tuple[float, ...],
    score_thresholds: tuple[float, ...],
) -> dict[str, Any]:
    settings = Settings(
        environment="test",
        database_url=database_url,
        auto_create_tables=True,
    )
    engine = build_engine(settings.database_url)
    create_all(engine)
    session_factory = build_session_factory(engine)
    seed_all(session_factory)
    skipped: list[str] = []
    provider: EmbeddingProvider | None = None
    provider_label = ""
    try:
        provider, provider_label = _dense_provider(settings)
        with session_factory() as session:
            try:
                store_embeddings(session, provider)
                session.commit()
            except ValueError as exc:
                session.rollback()
                skipped.append(f"dense index: {exc}")
                provider, provider_label = None, "skipped"

        with session_factory() as session:
            cases = _collect(session, dataset, provider)
            misses = _misses(cases)
            bm25 = _measure(
                session,
                cases,
                provider=None,
                gate=None,
                max_k=max(DEFAULT_K),
                settings=settings,
            )
            production_gate = DenseGate(
                min_top1_score=settings.retrieval_dense_gate_min_top1_score,
                min_coverage=settings.retrieval_dense_gate_min_coverage,
            )
            gated = _measure(
                session,
                cases,
                provider,
                production_gate,
                max_k=max(DEFAULT_K),
                settings=settings,
            )
            coverage_rows: list[dict[str, Any]] = []
            score_rows: list[dict[str, Any]] = []
            for threshold in coverage_thresholds:
                metrics = _measure(
                    session,
                    cases,
                    provider,
                    DenseGate(min_coverage=threshold),
                    max_k=max(DEFAULT_K),
                    settings=settings,
                )
                coverage_rows.append(
                    {"kind": "coverage", "threshold": threshold, "metrics": metrics}
                )
            for threshold in score_thresholds:
                metrics = _measure(
                    session,
                    cases,
                    provider,
                    DenseGate(min_top1_score=threshold, min_coverage=-1.0),
                    max_k=max(DEFAULT_K),
                    settings=settings,
                )
                score_rows.append(
                    {"kind": "score", "threshold": threshold, "metrics": metrics}
                )

            result = {
                "provider": provider_label,
                "bm25": bm25,
                "gate": {
                    "min_top1_score": production_gate.min_top1_score,
                    "min_coverage": production_gate.min_coverage,
                    "metrics": gated,
                },
                "coverage_sweep": coverage_rows,
                "score_sweep": score_rows,
                "cases": [
                    {
                        "id": case.case_id,
                        "bm25_rank": case.bm25_rank,
                        "dense_rank": case.dense_rank,
                        "top1_score": round(case.top1_score, 4),
                        "gap": round(case.gap, 4),
                        "coverage": round(case.coverage, 4),
                        "relevant": len(case.relevant),
                    }
                    for case in cases
                ],
                "misses": [case.case_id for case in misses],
                "skipped": skipped,
            }
    finally:
        engine.dispose()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyse the dense gate on the labelled retrieval set."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument(
        "--database-url",
        help="Run against this database instead of a temporary SQLite file.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--coverage-thresholds",
        default=",".join(str(value) for value in DEFAULT_COVERAGE_THRESHOLDS),
    )
    parser.add_argument(
        "--score-thresholds",
        default=",".join(str(value) for value in DEFAULT_SCORE_THRESHOLDS),
    )
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    coverage_thresholds = tuple(
        float(part) for part in args.coverage_thresholds.split(",") if part.strip()
    )
    score_thresholds = tuple(
        float(part) for part in args.score_thresholds.split(",") if part.strip()
    )

    if args.database_url:
        result = _run(
            dataset,
            args.database_url,
            coverage_thresholds=coverage_thresholds,
            score_thresholds=score_thresholds,
        )
    else:
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite+pysqlite:///{Path(directory) / 'gate.db'}"
            result = _run(
                dataset,
                url,
                coverage_thresholds=coverage_thresholds,
                score_thresholds=score_thresholds,
            )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    coverage_threshold = result["gate"]["min_coverage"]
    print(f"dense provider: {result['provider']}")
    print("")
    print(
        _format_features(
            _cases_from(result),
            coverage_threshold=result["gate"]["min_coverage"],
            top1_threshold=result["gate"]["min_top1_score"],
        )
    )
    print("")
    misses = _misses_from(result)
    print(_format_misses(misses))
    print("")
    print(
        _format_sweep(
            result["coverage_sweep"],
            label="coverage gate sweep (min_top1_score kept at the shipped value)",
            caught=[_caught(misses, row) for row in result["coverage_sweep"]],
            total_misses=len(misses),
        )
    )
    print("")
    print(
        _format_sweep(
            result["score_sweep"],
            label="score-only gate sweep (coverage disabled)",
            caught=[_caught(misses, row) for row in result["score_sweep"]],
            total_misses=len(misses),
        )
    )
    print("")
    print(
        f"bm25: recall@1 {result['bm25']['recall@1']:.4f} / @3 "
        f"{result['bm25']['recall@3']:.4f} / @5 {result['bm25']['recall@5']:.4f} "
        f"mrr {result['bm25']['mrr']:.4f} ndcg@5 {result['bm25']['ndcg@5']:.4f}"
    )
    gate_metrics = result["gate"]["metrics"]
    print(
        f"gated (<= {coverage_threshold:.2f}): recall@1 "
        f"{gate_metrics['recall@1']:.4f} / @3 {gate_metrics['recall@3']:.4f} / @5 "
        f"{gate_metrics['recall@5']:.4f} mrr {gate_metrics['mrr']:.4f} "
        f"ndcg@5 {gate_metrics['ndcg@5']:.4f}; dense calls "
        f"{gate_metrics['dense_calls']}/{gate_metrics['cases']}"
    )
    for note in result["skipped"]:
        print(f"skipped: {note}")
    return 0


# The JSON round-trip is lossy for Case objects; these two helpers rebuild the
# minimum needed for the text tables from the recorded rows.
def _cases_from(result: dict[str, Any]) -> list[Case]:
    return [
        Case(
            case_id=row["id"],
            query="",
            identity=Identity(tenant_id="", user_id="", roles=frozenset()),
            relevant=frozenset(),
            bm25_rank=row["bm25_rank"],
            dense_rank=row["dense_rank"],
            top1_score=row["top1_score"],
            gap=row["gap"],
            coverage=row["coverage"],
        )
        for row in result["cases"]
    ]


def _misses_from(result: dict[str, Any]) -> list[Case]:
    misses = set(result["misses"])
    return [case for case in _cases_from(result) if case.case_id in misses]


if __name__ == "__main__":
    sys.exit(main())
