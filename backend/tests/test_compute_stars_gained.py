"""_compute_stars_gained 单元测试 - 验证快照差值法与窗口校验."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.crawler.tasks import _compute_stars_gained
from app.models import Repository, Snapshot


def _make_repo(db, full_name="owner/repo", stars=1000, created_days_ago=365, pushed_days_ago=0):
    """创建一个 Repository 并入库."""
    today = date(2026, 7, 30)
    repo = Repository(
        github_id=hash(full_name) % 10**9,
        name=full_name.split("/", 1)[1],
        full_name=full_name,
        owner=full_name.split("/", 1)[0],
        html_url=f"https://github.com/{full_name}",
        stargazers_count=stars,
        created_at=date(2026, 7, 30) - timedelta(days=created_days_ago),
        pushed_at=date(2026, 7, 30) - timedelta(days=pushed_days_ago),
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


def _add_snapshot(db, repo, period, snap_date, stars_at_snap, score=1.0):
    """写入一条历史快照."""
    db.add(Snapshot(
        repository_id=repo.id,
        period=period,
        snapshot_date=snap_date,
        stars_at_snapshot=stars_at_snap,
        stars_gained=10,
        score=score,
    ))
    db.commit()


class TestDailyStarsGained:
    """daily period: 差值基准 = today - 1 (昨天)."""

    def test_daily_diff_against_yesterday(self, db_session, today):
        repo = _make_repo(db_session, stars=200)
        _add_snapshot(db_session, repo, "daily", today - timedelta(days=1), stars_at_snap=150)
        gained, is_estimated = _compute_stars_gained(db_session, repo, "daily", today, 200)
        assert gained == 50
        assert is_estimated is False

    def test_daily_no_snapshot_falls_back_to_estimation(self, db_session, today):
        repo = _make_repo(db_session, stars=200, created_days_ago=100)
        gained, is_estimated = _compute_stars_gained(db_session, repo, "daily", today, 200)
        # 估算: 200/100*1 = 2.0, 今天有push→1.3 → 2.6 → 2, 上限200
        assert is_estimated is True
        assert 0 <= gained <= 200


class TestWeeklyStarsGained:
    """weekly period: 差值基准 = today - 7 (7天前), 而非昨天 (核心修复点)."""

    def test_weekly_diff_against_7_days_ago(self, db_session, today):
        """关键: weekly 应对比 7 天前的快照, 而非昨天的快照."""
        repo = _make_repo(db_session, stars=500)
        # 昨天的 weekly 快照 (旧逻辑会错误地用这条, 得到 1 天增量)
        _add_snapshot(db_session, repo, "weekly", today - timedelta(days=1), stars_at_snap=490)
        # 7 天前的 weekly 快照 (新逻辑应该用这条)
        _add_snapshot(db_session, repo, "weekly", today - timedelta(days=7), stars_at_snap=400)
        gained, is_estimated = _compute_stars_gained(db_session, repo, "weekly", today, 500)
        # 修复后: 500 - 400 = 100 (7天增量), 而非 500 - 490 = 10 (1天增量)
        assert gained == 100
        assert is_estimated is False

    def test_weekly_only_yesterday_snapshot_window_too_recent(self, db_session, today):
        """仅有昨天的快照 (<= today-7 查不到) → 应降级估算."""
        repo = _make_repo(db_session, stars=500, created_days_ago=365, pushed_days_ago=1)
        _add_snapshot(db_session, repo, "weekly", today - timedelta(days=1), stars_at_snap=490)
        gained, is_estimated = _compute_stars_gained(db_session, repo, "weekly", today, 500)
        # 没有 <= today-7 的快照, 走估算
        assert is_estimated is True

    def test_weekly_window_anomaly_falls_back_to_estimation(self, db_session, today):
        """7天前快照存在但太老 (距 today > 7+2=9天) → 窗口异常, 降级估算."""
        repo = _make_repo(db_session, stars=500, created_days_ago=365, pushed_days_ago=1)
        # 15 天前的快照 (gap=15 > 9, 窗口异常)
        _add_snapshot(db_session, repo, "weekly", today - timedelta(days=15), stars_at_snap=400)
        gained, is_estimated = _compute_stars_gained(db_session, repo, "weekly", today, 500)
        assert is_estimated is True


class TestMonthlyStarsGained:
    """monthly period: 差值基准 = today - 30 (30天前)."""

    def test_monthly_diff_against_30_days_ago(self, db_session, today):
        repo = _make_repo(db_session, stars=2000)
        _add_snapshot(db_session, repo, "monthly", today - timedelta(days=30), stars_at_snap=1500)
        gained, is_estimated = _compute_stars_gained(db_session, repo, "monthly", today, 2000)
        assert gained == 500
        assert is_estimated is False

    def test_monthly_insufficient_history_falls_back(self, db_session, today):
        """系统运行不足 30 天, 无 30 天前快照 → 估算."""
        repo = _make_repo(db_session, stars=2000, created_days_ago=400, pushed_days_ago=1)
        _add_snapshot(db_session, repo, "monthly", today - timedelta(days=10), stars_at_snap=1800)
        gained, is_estimated = _compute_stars_gained(db_session, repo, "monthly", today, 2000)
        assert is_estimated is True
