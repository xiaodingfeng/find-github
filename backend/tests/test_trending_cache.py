"""Trending 缓存单元测试 - 验证按语言缓存写入与读取."""

from __future__ import annotations

from datetime import date

from app.crawler.tasks import _rebuild_trending_cache
from app.models import Repository, Snapshot, TrendingCache


def _make_repo(db, full_name, lang, stars=100):
    repo = Repository(
        github_id=hash(full_name) % 10**9,
        name=full_name.split("/", 1)[1],
        full_name=full_name,
        owner=full_name.split("/", 1)[0],
        html_url=f"https://github.com/{full_name}",
        stargazers_count=stars,
        language=lang,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


class TestTrendingCacheLanguage:
    """B3: 按 DEFAULT_TOP_LANGUAGES 写入各语言缓存."""

    def test_all_plus_per_language_caches_written(self, db_session, today):
        """抓取后应写入 language=all + 各热门语言 (小写) 的缓存."""
        py_repo = _make_repo(db_session, "a/py1", "Python", 500)
        js_repo = _make_repo(db_session, "b/js1", "JavaScript", 400)
        ruby_repo = _make_repo(db_session, "c/rb1", "Ruby", 300)

        trending = [
            (py_repo, 100, 50.0, False),
            (js_repo, 80, 40.0, False),
            (ruby_repo, 60, 30.0, False),
        ]
        _rebuild_trending_cache(db_session, "daily", today, trending)

        # language=all 应有 3 条
        all_rows = db_session.query(TrendingCache).filter_by(
            time_window="daily", language="all"
        ).order_by(TrendingCache.rank).all()
        assert len(all_rows) == 3
        assert all_rows[0].repository_id == py_repo.id  # score 最高

        # python (小写) 应有 1 条
        py_rows = db_session.query(TrendingCache).filter_by(
            time_window="daily", language="python"
        ).all()
        assert len(py_rows) == 1
        assert py_rows[0].repository_id == py_repo.id
        assert py_rows[0].rank == 1

        # javascript (小写) 应有 1 条
        js_rows = db_session.query(TrendingCache).filter_by(
            time_window="daily", language="javascript"
        ).all()
        assert len(js_rows) == 1

        # ruby (小写) 应有 1 条 (Ruby 在 DEFAULT_TOP_LANGUAGES 中)
        rb_rows = db_session.query(TrendingCache).filter_by(
            time_window="daily", language="ruby"
        ).all()
        assert len(rb_rows) == 1

    def test_per_language_rank_within_group(self, db_session, today):
        """同一语言多个仓库, rank 应在组内按 score 降序 1..N."""
        py1 = _make_repo(db_session, "a/py1", "Python", 500)
        py2 = _make_repo(db_session, "a/py2", "Python", 400)
        py3 = _make_repo(db_session, "a/py3", "Python", 300)

        trending = [
            (py1, 100, 50.0, False),
            (py2, 80, 40.0, False),
            (py3, 60, 30.0, False),
        ]
        _rebuild_trending_cache(db_session, "daily", today, trending)

        py_rows = db_session.query(TrendingCache).filter_by(
            time_window="daily", language="python"
        ).order_by(TrendingCache.rank).all()
        assert [r.rank for r in py_rows] == [1, 2, 3]
        assert py_rows[0].repository_id == py1.id

    def test_rebuild_clears_old_cache(self, db_session, today):
        """重建缓存应清除该 period 的旧缓存 (all + 各语言)."""
        repo = _make_repo(db_session, "a/py1", "Python", 500)
        db_session.add(TrendingCache(
            time_window="daily", language="all", repository_id=repo.id,
            rank=1, delta_stars=999, score=999.0,
        ))
        db_session.add(TrendingCache(
            time_window="daily", language="python", repository_id=repo.id,
            rank=1, delta_stars=999, score=999.0,
        ))
        db_session.commit()

        trending = [(repo, 100, 50.0, False)]
        _rebuild_trending_cache(db_session, "daily", today, trending)

        all_rows = db_session.query(TrendingCache).filter_by(
            time_window="daily", language="all"
        ).all()
        assert len(all_rows) == 1
        assert all_rows[0].delta_stars == 100  # 已被新值覆盖, 非 999
