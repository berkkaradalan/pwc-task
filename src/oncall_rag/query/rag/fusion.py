"""Reciprocal Rank Fusion: merges ranked lists by summing 1 / (k + rank) for each item."""

from collections.abc import Sequence

from oncall_rag.shared.models import Passage

# The constant from the original RRF paper: it damps the lead of the very top ranks, so an item
# ranked well by both lists beats one ranked first by only one of them.
RRF_K = 60


def reciprocal_rank_fusion(rankings: Sequence[Sequence[Passage]]) -> list[Passage]:
    """All passages of `rankings`, best fused score first. Ties keep first-seen order."""
    fused_scores: dict[Passage, float] = {}
    for ranking in rankings:
        for rank, passage in enumerate(ranking, start=1):
            fused_scores[passage] = fused_scores.get(passage, 0.0) + 1 / (RRF_K + rank)
    return sorted(fused_scores, key=fused_scores.__getitem__, reverse=True)
