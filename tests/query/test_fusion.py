from oncall_rag.query.rag.fusion import reciprocal_rank_fusion
from oncall_rag.shared.models import Passage


def passage(source: str) -> Passage:
    return Passage(source=source, title=source, heading_path="", text=source, score=0.5)


A, B, C, D = (passage(source) for source in ["a.md", "b.md", "c.md", "d.md"])


def test_an_item_found_by_both_lists_beats_one_ranked_first_by_only_one() -> None:
    fused = reciprocal_rank_fusion([[A, B, C], [C, D]])

    assert fused[0] == C


def test_every_item_appears_once() -> None:
    fused = reciprocal_rank_fusion([[A, B], [B, A, D]])

    assert sorted(p.source for p in fused) == ["a.md", "b.md", "d.md"]


def test_equal_scores_keep_first_seen_order() -> None:
    fused = reciprocal_rank_fusion([[A, B], [C, D]])

    # A and C share rank 1, B and D rank 2; the first list is seen first.
    assert fused == [A, C, B, D]


def test_empty_lists_give_no_items() -> None:
    assert reciprocal_rank_fusion([[], []]) == []
