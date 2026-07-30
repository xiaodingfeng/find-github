"""_compute_score 单元测试 - 验证低基数惩罚与 age 衰减下限."""

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
        # 10★ 应明显低于 50★ (因 penalty 0.2 vs 1.0)
        assert score_small < score_50 * 0.3

    def test_threshold_boundary(self, today):
        """total_stars=50 (阈值边界) penalty=1.0, 与 51 等价."""
        repo = _make_repo(created_days_ago=10)
        score_at_threshold = _compute_score(repo, stars_gained=30, total_stars=50, today=today)
        score_above = _compute_score(repo, stars_gained=30, total_stars=51, today=today)
        # 50 和 51 的 log_penalty 略有差异, 但 penalty 因子都是 1.0, 应非常接近
        assert abs(score_at_threshold - score_above) < 0.5

    def test_small_repo_penalty_value(self, today):
        """10★ → penalty=0.2, 验证具体数值."""
        import math
        repo = _make_repo(created_days_ago=1)  # age=1, age_factor≈1
        score = _compute_score(repo, stars_gained=10, total_stars=10, today=today)
        expected_penalty = 10 / SMALL_REPO_THRESHOLD  # 0.2
        expected_log = 1.0 / math.log10(10 + 10)  # 1/log10(20)≈0.7686
        expected_age = max(AGE_FACTOR_FLOOR, math.exp(-SCORE_LAMBDA * 1))
        assert abs(score - round(10 * expected_penalty * expected_log * expected_age, 4)) < 0.001


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
        # 反推 age_factor
        import math as m
        log_penalty = 1.0 / m.log10(1000 + 10)
        implied_age = score_with_floor / (100 * 1.0 * log_penalty)
        assert implied_age >= AGE_FACTOR_FLOOR - 0.01

    def test_young_repo_not_affected_by_floor(self, today):
        """1 年仓库: age_factor≈0.69 > 0.3, 不受下限影响."""
        import math
        repo = _make_repo(created_days_ago=365)
        score = _compute_score(repo, stars_gained=100, total_stars=1000, today=today)
        log_penalty = 1.0 / math.log10(1000 + 10)
        expected_age = math.exp(-SCORE_LAMBDA * 365)  # ≈0.694
        assert abs(score - round(100 * 1.0 * log_penalty * expected_age, 4)) < 0.001
