"""分类配额去同质化测试 - 验证 _diversify_trending.

模拟 monthly 榜单被单一高增长类别 (ai) 霸榜的场景, 确认配额重排后
靠前位置类别多样, 且不减少榜单总量.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.crawler.tasks import DIVERSITY_PER_CATEGORY_MIN, _diversify_trending


def _item(repo_id: int, category, score: float, gained: int = 10, est: bool = False):
    """构造 trending 元组 (repo, gained, score, est). repo 仅需 id + category."""
    repo = SimpleNamespace(id=repo_id, category=category)
    return (repo, gained, score, est)


class TestDiversifyTrending:
    def test_empty(self):
        assert _diversify_trending([], DIVERSITY_PER_CATEGORY_MIN) == []

    def test_per_category_min_zero_returns_original(self):
        items = [_item(1, "ai", 100), _item(2, "devops", 50)]
        # per_category_min <= 0 直接返回原对象
        assert _diversify_trending(items, 0) is items

    def test_total_count_unchanged(self):
        items = [_item(i, "ai", 100 - i) for i in range(10)]
        out = _diversify_trending(items, DIVERSITY_PER_CATEGORY_MIN)
        assert len(out) == len(items)

    def test_single_category_preserves_score_order(self):
        """单类别: 配额后整体仍按 score 降序 (基底=前N, 剩余接后, 均降序)."""
        items = [_item(i, "ai", 100 - i * 10) for i in range(6)]
        out = _diversify_trending(items, DIVERSITY_PER_CATEGORY_MIN)
        scores = [it[2] for it in out]
        assert scores == sorted(scores, reverse=True)

    def test_base_contains_each_category(self):
        """多类别: 基底 (前 cat*min 个) 内包含所有类别."""
        items = []
        items += [_item(i, "ai", 100 - i) for i in range(5)]            # ai 5 个
        items += [_item(10 + i, "devops", 50 - i) for i in range(3)]    # devops 3 个
        items += [_item(20 + i, "security", 45 - i) for i in range(3)]  # security 3 个
        items.sort(key=lambda x: x[2], reverse=True)  # 入参须按 score 降序

        out = _diversify_trending(items, DIVERSITY_PER_CATEGORY_MIN)
        base = out[:9]  # 3 类 × 3
        cats_in_base = {it[0].category for it in base}
        assert cats_in_base == {"ai", "devops", "security"}

    def test_monthly_ai_dominance_broken(self):
        """monthly 场景: ai 类 10 个高分霸榜, 配额后前 9 位 ai 仅占 3 个."""
        items = [_item(i, "ai", 100 - i * 5) for i in range(10)]          # ai 10 个 score 100..55
        items += [_item(100 + i, "devops", 50 - i) for i in range(3)]     # devops 3 个
        items += [_item(200 + i, "security", 45 - i) for i in range(3)]   # security 3 个
        items.sort(key=lambda x: x[2], reverse=True)

        out = _diversify_trending(items, DIVERSITY_PER_CATEGORY_MIN)
        # 前 9 (基底) 中 ai 不得超过 per_category_min
        ai_in_base = sum(1 for it in out[:9] if it[0].category == "ai")
        assert ai_in_base <= DIVERSITY_PER_CATEGORY_MIN
        # 前 9 内有 devops 与 security
        cats_top9 = {it[0].category for it in out[:9]}
        assert "devops" in cats_top9 and "security" in cats_top9
        # 总量不变
        assert len(out) == len(items)

    def test_base_sorted_by_score_desc(self):
        """基底跨桶合并后按 score 降序."""
        items = [_item(i, "ai", 100 - i) for i in range(3)]
        items += [_item(10 + i, "devops", 95 - i) for i in range(3)]
        items.sort(key=lambda x: x[2], reverse=True)

        out = _diversify_trending(items, DIVERSITY_PER_CATEGORY_MIN)
        base_scores = [it[2] for it in out[:6]]
        assert base_scores == sorted(base_scores, reverse=True)

    def test_none_category_bucketed(self):
        """category=None 归入 _uncategorized 桶, 同样参与配额."""
        items = [_item(1, None, 100), _item(2, None, 90), _item(3, "ai", 80)]
        items.sort(key=lambda x: x[2], reverse=True)

        out = _diversify_trending(items, DIVERSITY_PER_CATEGORY_MIN)
        # None 与 ai 各取 Top3 (候选不足按实际), 总量不变
        assert len(out) == 3

    def test_small_category_less_than_min(self):
        """某类候选 < min: 该类全部进基底, 不报错."""
        items = [_item(i, "ai", 100 - i) for i in range(5)]
        items += [_item(10, "security", 50)]  # security 仅 1 个
        items.sort(key=lambda x: x[2], reverse=True)

        out = _diversify_trending(items, DIVERSITY_PER_CATEGORY_MIN)
        # security 那 1 个应在基底内 (基底 ai3+sec1=4)
        base_cats = {it[0].category for it in out[:4]}
        assert "security" in base_cats
        assert len(out) == len(items)

    def test_no_duplicates(self):
        """结果无重复仓库."""
        items = [_item(i, "ai", 100 - i) for i in range(5)]
        items += [_item(10 + i, "devops", 50 - i) for i in range(3)]
        items.sort(key=lambda x: x[2], reverse=True)

        out = _diversify_trending(items, DIVERSITY_PER_CATEGORY_MIN)
        ids = [it[0].id for it in out]
        assert len(ids) == len(set(ids))
