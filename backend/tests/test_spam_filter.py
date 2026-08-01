"""反作弊过滤层测试 - 验证 _is_spam_repo 与 _compute_spam_owners.

模拟生产数据中的刷星营销号 (alphaparkinc/* 同 owner 9 个仓库均 9★/9 gained)
与游戏作弊器 (Palworld-Optimizer/call-of-duty-mod-menu), 确认被过滤层拦截.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.crawler.tasks import (
    QUALITY_MIN_DESC_LEN,
    QUALITY_MIN_STARS,
    SPAM_OWNER_MAX_STARS,
    SPAM_OWNER_MIN_REPOS,
    _compute_spam_owners,
    _is_spam_repo,
)
from app.models import Repository


_github_id_counter = 0


def _make_repo(
    full_name="owner/repo",
    owner="owner",
    stars=100,
    desc="A useful tool for developers",
    first_seen_days_ago=0,
    today=date(2026, 7, 30),
):
    global _github_id_counter
    _github_id_counter += 1
    return Repository(
        github_id=_github_id_counter,
        name=full_name.split("/")[-1],
        full_name=full_name,
        owner=owner,
        html_url="x",
        description=desc,
        stargazers_count=stars,
        first_seen_at=today - timedelta(days=first_seen_days_ago),
    )


class TestIsSpamRepo:
    """_is_spam_repo 单条判定规则."""

    def test_low_stars_is_spam(self, today):
        """总 star < QUALITY_MIN_STARS (9★ 刷星仓) → spam."""
        repo = _make_repo(stars=9, desc="some description here")
        assert _is_spam_repo(repo, set()) is True

    def test_min_stars_boundary(self, today):
        """star=QUALITY_MIN_STARS 边界, 描述足够长 → 非 spam."""
        repo = _make_repo(stars=QUALITY_MIN_STARS, desc="A legitimate project with enough desc")
        assert _is_spam_repo(repo, set()) is False

    def test_short_description_is_spam(self, today):
        """描述长度 < QUALITY_MIN_DESC_LEN → spam (刷星仓多无描述或乱码)."""
        repo = _make_repo(stars=500, desc="short")  # stars 够高但描述过短
        assert _is_spam_repo(repo, set()) is True

    def test_none_description_is_spam(self, today):
        """无描述 → spam."""
        repo = _make_repo(stars=500, desc=None)
        assert _is_spam_repo(repo, set()) is True

    def test_spam_keyword_in_name(self, today):
        """仓库名含 SPAM_KEYWORDS (mod-menu/cheat/booster) → spam."""
        for kw in ["mod-menu", "cheat", "fps-booster", "script-hub", "Optimizer-2026"]:
            repo = _make_repo(
                full_name=f"evil/{kw}-tool",
                stars=500,
                desc="A long enough description here",
            )
            assert _is_spam_repo(repo, set()) is True, f"应识别 {kw} 为 spam"

    def test_owner_in_spam_set(self, today):
        """owner 在 spam_owners 集合 → spam (批量刷星检测)."""
        repo = _make_repo(
            owner="alphaparkinc",
            stars=25,  # 高于 MIN_STARS 但低于 SPAM_OWNER_MAX_STARS
            desc="genpark marketing skill tool",
        )
        assert _is_spam_repo(repo, {"alphaparkinc"}) is True

    def test_legitimate_repo_not_spam(self, today):
        """正常仓库 (高 star + 长描述 + 无黑名单词 + owner 不在 spam 集) → 非 spam."""
        repo = _make_repo(
            full_name="nushell/nushell",
            owner="nushell",
            stars=36888,
            desc="A new type of shell for the modern era",
        )
        assert _is_spam_repo(repo, set()) is False


class TestComputeSpamOwners:
    """_compute_spam_owners 批量检测: 同 owner 近7天低 star 仓库数 >= 3."""

    def test_single_owner_three_low_star_repos(self, db_session, today):
        """alphaparkinc 有 3 个近7天新增的低 star 仓库 → 进 spam_owners."""
        for i in range(SPAM_OWNER_MIN_REPOS):
            db_session.add(_make_repo(
                full_name=f"alphaparkinc/repo-{i}",
                owner="alphaparkinc",
                stars=9,
                desc="genpark marketing skill",
                first_seen_days_ago=i,
                today=today,
            ))
        db_session.commit()

        repos = db_session.query(Repository).all()
        spam_owners = _compute_spam_owners(db_session, repos, today)
        assert "alphaparkinc" in spam_owners

    def test_owner_below_threshold_not_spam(self, db_session, today):
        """owner 仅 2 个低 star 仓库 (< SPAM_OWNER_MIN_REPOS) → 非 spam."""
        for i in range(SPAM_OWNER_MIN_REPOS - 1):
            db_session.add(_make_repo(
                full_name=f"normal/repo-{i}",
                owner="normal",
                stars=9,
                desc="some desc",
                first_seen_days_ago=i,
                today=today,
            ))
        db_session.commit()

        repos = db_session.query(Repository).all()
        spam_owners = _compute_spam_owners(db_session, repos, today)
        assert "normal" not in spam_owners

    def test_old_low_star_repos_not_counted(self, db_session, today):
        """8 天前新增的低 star 仓库不计入 (超 7 天窗口)."""
        for i in range(SPAM_OWNER_MIN_REPOS):
            db_session.add(_make_repo(
                full_name=f"old/repo-{i}",
                owner="old",
                stars=9,
                desc="some desc",
                first_seen_days_ago=8,  # 超 7 天窗口
                today=today,
            ))
        db_session.commit()

        repos = db_session.query(Repository).all()
        spam_owners = _compute_spam_owners(db_session, repos, today)
        assert "old" not in spam_owners

    def test_high_star_owner_not_in_candidates(self, db_session, today):
        """候选 repos 中 owner 的仓库 star >= SPAM_OWNER_MAX_STARS → 不进查询."""
        repo = _make_repo(
            owner="bigcorp",
            stars=SPAM_OWNER_MAX_STARS + 100,  # 远超阈值
            desc="legitimate big project",
        )
        spam_owners = _compute_spam_owners(db_session, [repo], today)
        assert spam_owners == set()

    def test_empty_repos_returns_empty(self, db_session, today):
        """无候选 → 空集."""
        assert _compute_spam_owners(db_session, [], today) == set()
