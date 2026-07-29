"""/api/v1/trending 端点 - 高性能 Trending 榜单 (优先读 trending_cache).

对齐方案文档设计: GET /api/v1/trending?time_window=daily&language=all&limit=100&page=1
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Repository, Snapshot, TrendingCache
from ..schemas import RepositoryOut, TrendingItemOut, TrendingListOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/trending", tags=["trending"])


@router.get("", response_model=TrendingListOut)
def get_trending(
    time_window: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    language: str = Query("all"),
    limit: int = Query(50, ge=1, le=1000),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
) -> TrendingListOut:
    """获取 Trending 榜单 (优先读缓存, 缓存 miss 时实时计算).

    - time_window: daily / weekly / monthly
    - language: all / python / rust / ... (all 时读缓存, 其他语言实时计算)
    - limit: 返回数量 (最大 1000)
    - page: 页码
    """
    # language=all 时优先读缓存
    if language == "all":
        # 用 count + offset/limit 在 SQL 层分页, 避免把全部缓存行读入内存
        total = (
            db.query(func.count(TrendingCache.id))
            .filter(TrendingCache.time_window == time_window)
            .scalar()
        ) or 0
        if total > 0:
            page_rows = (
                db.query(TrendingCache, Repository)
                .join(Repository, Repository.id == TrendingCache.repository_id)
                .filter(TrendingCache.time_window == time_window)
                .order_by(TrendingCache.rank.asc())
                .offset((page - 1) * limit)
                .limit(limit)
                .all()
            )
            items = [
                TrendingItemOut(
                    rank=cache.rank,
                    repo=RepositoryOut.model_validate(repo),
                    delta_stars=cache.delta_stars,
                    score=cache.score,
                )
                for cache, repo in page_rows
            ]
            return TrendingListOut(
                time_window=time_window,
                language=language,
                total=total,
                page=page,
                limit=limit,
                items=items,
                cache_hit=True,
            )
        # 缓存 miss: 降级到实时计算
        logger.info("Trending cache miss for time_window=%s, computing in real-time", time_window)

    # 实时计算 (缓存 miss 或指定语言时)
    latest_date_subq = (
        db.query(
            Snapshot.repository_id.label("rid"),
            func.max(Snapshot.snapshot_date).label("max_date"),
        )
        .filter(Snapshot.period == time_window)
        .group_by(Snapshot.repository_id)
        .subquery()
    )
    base = (
        db.query(Repository, Snapshot.stars_gained, Snapshot.score, Snapshot.rank_in_period)
        .join(Snapshot, Snapshot.repository_id == Repository.id)
        .join(
            latest_date_subq,
            (latest_date_subq.c.rid == Repository.id)
            & (latest_date_subq.c.max_date == Snapshot.snapshot_date),
        )
        .filter(Snapshot.period == time_window)
    )
    if language != "all":
        base = base.filter(Repository.language == language.capitalize())

    total = base.count()
    rows = (
        base.order_by(desc(Snapshot.score))
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    items = []
    for idx, (repo, gained, score, _rank) in enumerate(rows, start=(page - 1) * limit + 1):
        items.append(
            TrendingItemOut(
                rank=idx,
                repo=RepositoryOut.model_validate(repo),
                delta_stars=int(gained or 0),
                score=float(score or 0.0),
            )
        )
    return TrendingListOut(
        time_window=time_window,
        language=language,
        total=total,
        page=page,
        limit=limit,
        items=items,
        cache_hit=False,
    )
