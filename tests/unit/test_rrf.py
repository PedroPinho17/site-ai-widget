from site_ai_widget.docs.rrf import reciprocal_rank_fusion


def test_rrf_prefers_items_in_both_lists():
    a = [{"id": "x"}, {"id": "y"}, {"id": "z"}]
    b = [{"id": "z"}, {"id": "x"}, {"id": "w"}]
    fused = reciprocal_rank_fusion([a, b], k=60, top_n=3)
    ids = [r["id"] for r in fused]
    assert ids[0] in ("x", "z")  # appear in both
    assert "w" in ids or "y" in ids
    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_rrf_top_n():
    lists = [[{"id": i} for i in range(10)]]
    assert len(reciprocal_rank_fusion(lists, top_n=4)) == 4
