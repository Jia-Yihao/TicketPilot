"""Cross-channel rank fusion.

Pure functions, no third-party dependency, so both retrieval channels can share
one algorithm and the fusion order stays reproducible.

Contract
    incoming scores are always "bigger is better";
    ties keep the first-seen order (stable, replayable).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

__all__ = ["rrf_fuse", "zscore_fuse"]


def rrf_fuse(
    ranked_ids: Sequence[Iterable[str]],
    *,
    k: int = 60,
    top_n: int | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: ``score(d) = Σ_i 1 / (k + rank_i(d))`` (rank starts at 1).

    - a larger ``k`` smooths the gap between channels; a smaller ``k`` sharpens
      the head of the list, which suits short conversations;
    - a repeated id inside one channel only counts once; the same id found in
      several channels is merged and its scores add up;
    - ties keep the first-seen order;
    - ``top_n > 0`` truncates the result.
    """
    eff_k = max(1, int(k))
    scores: dict[str, float] = {}
    order: list[str] = []
    for ids in ranked_ids or []:
        if not ids:
            continue
        seen: set[str] = set()
        for rank, doc_id in enumerate(ids, start=1):
            if not doc_id or doc_id in seen:
                continue
            seen.add(doc_id)
            if doc_id not in scores:
                scores[doc_id] = 0.0
                order.append(doc_id)
            scores[doc_id] += 1.0 / (eff_k + rank)
    ranked = sorted(order, key=lambda doc_id: -scores[doc_id])
    result = [(doc_id, scores[doc_id]) for doc_id in ranked]
    if top_n is not None and top_n > 0:
        result = result[:top_n]
    return result


def zscore_fuse(
    channels: Sequence[Sequence[tuple[str, float]]],
    *,
    alpha: float = 0.5,
    top_n: int | None = None,
) -> list[tuple[str, float]]:
    """Weighted sum of per-channel z-scores.

    - the first channel carries ``alpha``, the remaining channels share
      ``1 - alpha`` (``alpha`` is clamped to [0, 1]);
    - every channel is normalized on its own: ``z = (s - mean) / std``; a zero
      standard deviation scores the whole channel 0 instead of faking a
      distinction;
    - a repeated id inside one channel only counts once; the same id found in
      several channels is merged by weighted sum;
    - ties keep the first-seen order; ``top_n > 0`` truncates the result.
    """
    chan_list = [list(channel or ()) for channel in (channels or ())]
    n_ch = len(chan_list)
    if n_ch == 0:
        return []
    eff_alpha = min(1.0, max(0.0, float(alpha)))
    if n_ch == 1:
        weights = [1.0]
    else:
        rest = (1.0 - eff_alpha) / (n_ch - 1)
        weights = [eff_alpha, *([rest] * (n_ch - 1))]

    scores: dict[str, float] = {}
    order: list[str] = []
    for weight, channel in zip(weights, chan_list, strict=True):
        if not channel or weight == 0.0:
            continue
        values = [float(score) for _, score in channel]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        std = variance**0.5
        for doc_id, raw in channel:
            if not doc_id:
                continue
            if doc_id not in scores:
                scores[doc_id] = 0.0
                order.append(doc_id)
            z = 0.0 if std <= 0 else (float(raw) - mean) / std
            scores[doc_id] += weight * z
    ranked = sorted(order, key=lambda doc_id: -scores[doc_id])
    result = [(doc_id, scores[doc_id]) for doc_id in ranked]
    if top_n is not None and top_n > 0:
        result = result[:top_n]
    return result
