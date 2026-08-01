"""_compute_score 单元测试 - 验证低基数惩罚、age 衰减下限与 growth_ratio 因子."""

from __future__ import annotations

from datetime import date, timedelta

from app.crawler.tasks import (
    AGE_FACTOR_FLOOR,
    SCORE_LAMBDA,
    SMALL_REPO_THRESHOLD,
    _compute_score,
)
from app.models import Repository


def _make_repo(created_days_ago=365):
    today = date(2026, 7, 30)
    return Repository(
        github_id=1,
        name="repo",
        full_name="owner/repo",
        owner="owner",
        html_url="x",
        stargazers_count=100,
        created_at=today - timedelta(days=created_days_ago),
    )


class TestSmallRepoPenalty:
    """2.1 低基数惩罚: total_stars<50 时线性衰减."""

    def test_zero_stars_zero_score(self, today):
        repo = _make_repo()
        # stars_gained=0 → score=0
        assert _compute_score(repo, stars_gained=0, total_stars=0, today=today) == 0.0

    def test_small_repo_penalized(self, today):
        """10★ 仓库: penalty=0.2, 显著低于 50★ 仓库."""
        repo = _make_repo(created_days_ago=10)
        score_small = _compute_score(repo, stars_gained=30, total_stars=10, today=today)
        score_50 = _compute_score(repo, stars_gained=30, total_stars=50, today=today)
        # 10★ 应明显低于 50★. penalty 0.2 vs 1.0 是主因 (log_penalty 与 growth_factor
        # 部分抵消: small 的 log 更大且 growth clamp 到 1.5). 实测比例约 0.37.
        assert score_small < score_50 * 0.4

    def test_threshold_boundary(self, today):
        """total_stars=50 (阈值边界) penalty=1.0, 与 51 等价."""
        repo = _make_repo(created_days_ago=10)
        score_at_threshold = _compute_score(repo, stars_gained=30, total_stars=50, today=today)
        score_above = _compute_score(repo, stars_gained=30, total_stars=51, today=today)
        # 50 和 51 的 log_penalty 略有差异, 但 penalty 因子都是 1.0, 应非常接近
        assert abs(score_at_threshold - score_above) < 0.5

    def test_small_repo_penalty_value(self, today):
        """10★ → penalty=0.2, growth_ratio=1.0 → factor=1.5, 验证具体数值."""
        import math
        repo = _make_repo(created_days_ago=1)  # age=1, age_factor≈1
        score = _compute_score(repo, stars_gained=10, total_stars=10, today=today)
        expected_penalty = 10 / SMALL_REPO_THRESHOLD  # 0.2
        expected_log = 1.0 / math.log10(10 + 10)  # 1/log10(20)≈0.7686
        expected_age = max(AGE_FACTOR_FLOOR, math.exp(-SCORE_LAMBDA * 1))
        expected_growth = 0.5 + min(1.0, 10 / 10)  # ratio=1.0 → 1.5
        assert abs(score - round(10 * expected_penalty * expected_log * expected_age * expected_growth, 4)) < 0.001


class TestAgeFloor:
    """2.2 age 衰减下限: 老仓库因子不低于 0.3."""

    def test_old_repo_age_factor_floored(self, today):
        """10 年仓库: 原 e^(-0.001*3650)≈0.026, 应被抬到 0.3."""
        import math
        repo = _make_repo(created_days_ago=3650)
        # age_factor 应为 0.3 (下限), 而非 0.026
        raw_age = math.exp(-SCORE_LAMBDA * 3650)
        assert raw_age < 0.03  # 原始值确实很低
        # 10 年仓库 score 应明显高于无下限时的值
        score_with_floor = _compute_score(repo, stars_gained=100, total_stars=1000, today=today)
        # 反推 age_factor (需扣除 growth_factor: 0.5 + 100/1000 = 0.6)
        import math as m
        log_penalty = 1.0 / m.log10(1000 + 10)
        growth_factor = 0.5 + min(1.0, 100 / 1000)  # 0.6
        implied_age = score_with_floor / (100 * 1.0 * log_penalty * growth_factor)
        assert implied_age >= AGE_FACTOR_FLOOR - 0.01

    def test_young_repo_not_affected_by_floor(self, today):
        """1 年仓库: age_factor≈0.69 > 0.3, 不受下限影响."""
        import math
        repo = _make_repo(created_days_ago=365)
        score = _compute_score(repo, stars_gained=100, total_stars=1000, today=today)
        log_penalty = 1.0 / math.log10(1000 + 10)
        expected_age = math.exp(-SCORE_LAMBDA * 365)  # ≈0.694
        expected_growth = 0.5 + min(1.0, 100 / 1000)  # 0.6
        assert abs(score - round(100 * 1.0 * log_penalty * expected_age * expected_growth, 4)) < 0.001


class TestGrowthRatio:
    """growth_ratio 因子: 增量占比 stars_gained/total_stars, 抑制大仓库霸榜."""

    def test_big_repo_suppressed_vs_small_darkhorse(self, today):
        """同 ΔStars 下, 大仓库 (增量占比低) score 应低于小黑马 (增量占比高).

        模拟: openclaw 384749★ gained 60016 vs deedy 258★ gained 135.
        虽然两者 ΔStars 差距大, 但 growth_ratio 让小马马的 score 相对其 ΔStars 被放大,
        大仓库相对其 ΔStars 被压缩. 此处用控制变量: 同 gained 验证 growth 效果.
        """
        repo = _make_repo(created_days_ago=100)
        # 同 gained=50, age 相同, 但 total_stars 不同
        score_big = _compute_score(repo, stars_gained=50, total_stars=50000, today=today)
        score_small = _compute_score(repo, stars_gained=50, total_stars=100, today=today)
        # 大仓库 ratio=0.001→factor=0.501; 小仓库 ratio=0.5→factor=1.0
        # 小仓库 score 应约为大仓库的 1.0/0.501 ≈ 2 倍 (log_penalty 也不同, 但同向)
        assert score_small > score_big

    def test_growth_ratio_clamped(self, today):
        """gained > total_stars (估算值极端情况) 时 ratio 被 clamp 到 1.0, factor=1.5."""
        import math
        repo = _make_repo(created_days_ago=1)
        # gained=200, total_stars=100 → ratio 未 clamp 应为 2.0, clamp 后 1.0
        score = _compute_score(repo, stars_gained=200, total_stars=100, today=today)
        penalty = 1.0  # total_stars=100 > 50
        log_penalty = 1.0 / math.log10(100 + 10)
        age_factor = max(AGE_FACTOR_FLOOR, math.exp(-SCORE_LAMBDA * 1))
        expected_growth = 0.5 + 1.0  # clamp 后 ratio=1.0 → 1.5
        assert abs(score - round(200 * penalty * log_penalty * age_factor * expected_growth, 4)) < 0.001

    def test_growth_factor_range(self, today):
        """growth_factor 范围 [0.5, 1.5]: ratio=0 → 0.5, ratio>=1 → 1.5."""
        import math
        repo = _make_repo(created_days_ago=1)
        log_penalty = 1.0 / math.log10(100 + 10)
        age_factor = max(AGE_FACTOR_FLOOR, math.exp(-SCORE_LAMBDA * 1))
        base = 50 * 1.0 * log_penalty * age_factor  # gained=50, total=100, penalty=1.0

        # ratio=0.1 (gained=10, total=100) → factor 0.6
        score_low = _compute_score(repo, stars_gained=10, total_stars=100, today=today)
        low_base = 10 * 1.0 * (1.0 / math.log10(110)) * age_factor
        assert abs(score_low - round(low_base * 0.6, 4)) < 0.001

        # ratio=1.0 (gained=100, total=100) → factor 1.5
        score_high = _compute_score(repo, stars_gained=100, total_stars=100, today=today)
        high_base = 100 * 1.0 * (1.0 / math.log10(110)) * age_factor
        assert abs(score_high - round(high_base * 1.5, 4)) < 0.001
