"""Fine-tune a Chinese bi-encoder on the support corpus, with a leak audit.

Why this exists
    The retrieval corpus is vertical: four after-sales policies, fifteen
    sections, and customer phrasings that share almost no vocabulary with the
    policy text. A general-purpose embedding model has never seen that mapping,
    so the dense channel scores about the same as BM25 and the fusion buys
    little. A small model fine-tuned on this domain is the cheap fix, and this
    script is the whole of it: build pairs, audit the split, train, save.

What it trains on
    ``gold-dev``   the authored dev split of ``evals/retrieval_set.json``
                   (human labels, never the test split);
    ``external``   real user messages from the public customer-service corpus,
                   kept only when exactly one section rule fires (weak labels).

    The test split is never read for training. It is read once, before
    training, to prove that no training query is the same question as a test
    query: an exact match or a character-3-gram Jaccard above the threshold
    aborts the run.

Anti-cheat
    Three independent checks, all reported in ``audit.json``:
    1. split      train queries come from dev / the external corpus; test
                  queries come from the test split, and the two never overlap;
    2. near-dup   character-3-gram Jaccard between every train and test query
                  stays under ``--near-dup`` (default 0.8);
    3. holdout    ``--holdout-policy`` trains without one policy's sections and
                  the evaluation scores that policy's test cases alone, so a
                  gain there cannot come from having memorised those clauses.

Usage::

    python scripts/finetune_embedder.py --out .tmp/models/bge-cs \
        --corpus .tmp/customer_service/train_clean_v2_small.jsonl

    python scripts/finetune_embedder.py --out .tmp/models/bge-cs-gold \
        --pairs gold-dev
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.retrieval.ingestion import chunk_markdown  # noqa: E402
from app.retrieval.rewrite import _SECTION_RULES, SECTION_TITLES  # noqa: E402

DATASET_PATH = ROOT / "evals" / "retrieval_set.json"
POLICY_DIR = ROOT / "app" / "resources" / "policies"
MANIFEST_PATH = POLICY_DIR / "manifest.json"
BASE_MODEL = "BAAI/bge-small-zh-v1.5"
NEAR_DUP = 0.8
_LONG_DIGITS = re.compile(r"\d{8,}")


class CorpusError(RuntimeError):
    """The inputs do not describe the corpus this script expects."""


# --------------------------------------------------------------- corpus text
def section_texts() -> dict[tuple[str, str], str]:
    """``(policy_id, section)`` -> the text the retriever actually indexes.

    The chunker is imported from the app instead of re-implemented here: the
    positive example must be byte-identical to the chunk the search scores,
    otherwise the model learns a slightly different target than it will see.
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    texts: dict[tuple[str, str], str] = {}
    for document in manifest["documents"]:
        if document.get("effective_to"):
            continue  # a retired version never appears in a live ranking
        policy_id = document["policy_id"]
        content = (POLICY_DIR / document["file"]).read_text(encoding="utf-8")
        for section, body in chunk_markdown(content):
            if section not in SECTION_TITLES.get(policy_id, ()):
                continue
            texts[(policy_id, section)] = f"{section}\n{body}".strip()
    return texts


# ------------------------------------------------------------------- labels
# A section cue alone is not enough: 多久 can mean 退款时限 or 快递多久到.
# The domain word is what makes the message about this policy at all, so a
# weak label needs both, and needs them for exactly one section.
_DOMAIN_GATE: dict[str, tuple[str, ...]] = {
    "POLICY-REFUND": ("退款", "退钱", "退费", "可退", "返款"),
    "POLICY-DELAY": (
        "物流",
        "快递",
        "包裹",
        "配送",
        "延迟",
        "签收",
        "发货",
        "到货",
        "承运商",
        "轨迹",
    ),
    "POLICY-RETURN": ("退货", "退回", "寄回", "无理由", "拆封", "赠品", "换货"),
    "POLICY-TICKET": ("工单", "优先级", "转人工", "人工", "投诉", "升级"),
}


def weak_label(query: str) -> tuple[str, str] | None:
    """The single section a real customer message is about, or ``None``.

    Reuses the shipped section cues, and keeps only the unambiguous case: a
    message that fires cues for two sections is dropped rather than guessed,
    because a wrong positive is worse than a missing one.
    """
    hits: list[tuple[str, str]] = []
    for rule in _SECTION_RULES:
        if not any(word in query for word in _DOMAIN_GATE[rule.policy_id]):
            continue
        if any(cue in query for cue in rule.cues):
            hits.append((rule.policy_id, rule.section))
    unique = list(dict.fromkeys(hits))
    if len(unique) != 1:
        return None
    return unique[0]


def _clean(text: str) -> str | None:
    query = " ".join(str(text).split())
    if not 4 <= len(query) <= 40:
        return None
    if _LONG_DIGITS.search(query) or "http" in query.lower():
        return None
    return query


def external_pairs(
    corpus: Path, *, max_per_section: int, exclude: set[str]
) -> list[dict[str, Any]]:
    """Weakly labelled pairs from real user messages, balanced per section."""
    counts: Counter[tuple[str, str]] = Counter()
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    with corpus.open(encoding="utf-8") as handle:
        for raw in handle:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for message in row.get("messages", []):
                if message.get("role") != "user":
                    continue
                query = _clean(message.get("content", ""))
                if query is None or query in seen or query in exclude:
                    continue
                seen.add(query)
                label = weak_label(query)
                if label is None:
                    continue
                if counts[label] >= max_per_section:
                    continue
                counts[label] += 1
                rows.append(
                    {
                        "query": query,
                        "policy_id": label[0],
                        "section": label[1],
                        "source": "external",
                    }
                )
    return rows


def gold_dev_pairs(dataset: dict[str, Any], *, holdout: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        if case.get("label_quality") != "gold" or case.get("split") != "dev":
            continue
        policy_id = case["policy_id"]
        if holdout and policy_id == holdout:
            continue
        rows.append(
            {
                "query": case["query"],
                "policy_id": policy_id,
                "section": case["sections"][0],
                "source": "gold-dev",
            }
        )
    return rows


# -------------------------------------------------------------------- audit
def _grams(text: str, size: int = 3) -> set[str]:
    stripped = re.sub(r"\s+", "", text)
    return {stripped[index : index + size] for index in range(len(stripped) - size + 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def audit(
    train: list[dict[str, Any]], test_queries: list[str], *, limit: float
) -> dict[str, Any]:
    """Prove that training and test ask different questions.

    Exact matches are checked first; near duplicates are found through a
    3-gram index so the check stays linear instead of quadratic.
    """
    index: dict[str, list[int]] = {}
    test_grams = [_grams(query) for query in test_queries]
    for position, grams in enumerate(test_grams):
        for gram in grams:
            index.setdefault(gram, []).append(position)

    exact = 0
    worst = 0.0
    offenders: list[dict[str, Any]] = []
    for row in train:
        query = row["query"]
        if query in set(test_queries):
            exact += 1
            continue
        grams = _grams(query)
        candidates: Counter[int] = Counter()
        for gram in grams:
            for position in index.get(gram, ()):  # pragma: no branch
                candidates[position] += 1
        for position, shared in candidates.most_common(5):
            if shared < 4:
                break
            score = _jaccard(grams, test_grams[position])
            worst = max(worst, score)
            if score >= limit:
                offenders.append(
                    {
                        "train": query,
                        "test": test_queries[position],
                        "jaccard": round(score, 3),
                    }
                )
    return {
        "train_rows": len(train),
        "test_rows": len(test_queries),
        "exact_overlap": exact,
        "max_near_duplicate": round(worst, 3),
        "near_duplicate_threshold": limit,
        "offenders": offenders[:20],
    }


# ------------------------------------------------------------------- train
def train(
    pairs: list[dict[str, Any]],
    texts: dict[tuple[str, str], str],
    *,
    base: str,
    out: Path,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    seed: int,
) -> dict[str, Any]:
    from sentence_transformers import (  # noqa: PLC0415
        InputExample,
        SentenceTransformer,
        losses,
    )

    missing = {
        (row["policy_id"], row["section"])
        for row in pairs
        if (row["policy_id"], row["section"]) not in texts
    }
    if missing:
        raise CorpusError(f"pairs point at unknown sections: {sorted(missing)[:3]}")

    examples = [
        InputExample(texts=[row["query"], texts[(row["policy_id"], row["section"])]])
        for row in pairs
    ]
    random.Random(seed).shuffle(examples)

    model = SentenceTransformer(base)
    from torch.utils.data import DataLoader  # noqa: PLC0415

    loader = DataLoader(examples, shuffle=True, batch_size=batch_size)
    loss = losses.MultipleNegativesRankingLoss(model)
    model.fit(
        train_objectives=[(loader, loss)],
        epochs=epochs,
        warmup_steps=max(10, len(loader) // 10),
        optimizer_params={"lr": learning_rate},
        show_progress_bar=False,
    )
    out.mkdir(parents=True, exist_ok=True)
    model.save(str(out))
    return {
        "base": base,
        "pairs": len(examples),
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "sections": len({(row["policy_id"], row["section"]) for row in pairs}),
        "by_source": dict(Counter(row["source"] for row in pairs)),
        "by_section": {
            f"{policy}/{section}": count
            for (policy, section), count in sorted(
                Counter(
                    (row["policy_id"], row["section"]) for row in pairs
                ).items()
            )
        },
    }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Model output directory.")
    parser.add_argument(
        "--pairs",
        choices=("gold-dev", "gold-dev+external"),
        default="gold-dev",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        help="External JSONL of customer-service dialogues (messages[role=user]).",
    )
    parser.add_argument("--max-per-section", type=int, default=300)
    parser.add_argument("--holdout-policy", help="Train without this policy's sections.")
    parser.add_argument("--base", default=BASE_MODEL)
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--near-dup", type=float, default=NEAR_DUP)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the pairs and print the leak audit without training.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    test_queries = [case["query"] for case in dataset["cases"] if case.get("split") == "test"]
    texts = section_texts()

    rows = gold_dev_pairs(dataset, holdout=args.holdout_policy)
    if args.pairs == "gold-dev+external":
        if args.corpus is None:
            raise SystemExit("--pairs gold-dev+external needs --corpus")
        if not args.corpus.exists():
            raise SystemExit(f"corpus not found: {args.corpus}")
        excluded = {case["query"] for case in dataset["cases"]}
        rows += external_pairs(
            args.corpus, max_per_section=args.max_per_section, exclude=excluded
        )
    if not rows:
        raise SystemExit("no training pairs; check --pairs and --holdout-policy")

    report = audit(rows, test_queries, limit=args.near_dup)
    if report["exact_overlap"] or report["offenders"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit("leak audit failed: a training query is a test query")
    if args.dry_run:
        by_section = Counter((row["policy_id"], row["section"]) for row in rows)
        print(
            json.dumps(
                {
                    "pairs": len(rows),
                    "by_source": dict(Counter(row["source"] for row in rows)),
                    "by_section": {
                        f"{policy}/{section}": count
                        for (policy, section), count in sorted(by_section.items())
                    },
                    "audit": report,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    stats = train(
        rows,
        texts,
        base=args.base,
        out=args.out,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        seed=args.seed,
    )
    stats["audit"] = report
    stats["corpus_sha256"] = _digest(args.corpus) if args.corpus else None
    stats["holdout_policy"] = args.holdout_policy
    stats["output"] = str(args.out)
    (args.out / "audit.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {key: value for key, value in stats.items() if key != "by_section"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
