"""/api/stats 端点 - 仪表盘统计聚合."""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, literal, or_
from sqlalchemy.orm import Session

from ..tz import now_cn

from ..database import get_db
from ..models import AIInterpretation, CrawlRun, Repository, Snapshot
from ..schemas import (
    AIInterpretationOut,
    CategoryStatOut,
    IndustryStatOut,
    LanguageStatOut,
    RadarPointOut,
    RepositoryOut,
    SummaryOut,
    TimelinePointOut,
    TopRepoOut,
)
from .repos import _attach_latest_interpretations

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/summary", response_model=SummaryOut)
def get_summary(db: Session = Depends(get_db)) -> SummaryOut:
    """仪表盘汇总: 总仓库数, 按 period 分布, 平均 star, 最近抓取, 扩展统计."""
    total_repos = db.query(func.count(Repository.id)).scalar() or 0
    total_runs = db.query(func.count(CrawlRun.id)).scalar() or 0

    by_period_rows = (
        db.query(Snapshot.period, func.count(func.distinct(Snapshot.repository_id)))
        .group_by(Snapshot.period)
        .all()
    )
    by_period = {p: c for p, c in by_period_rows}

    avg_stars = db.query(func.avg(Repository.stargazers_count)).scalar() or 0.0

    top_language_row = (
        db.query(Repository.language, func.count(Repository.id).label("cnt"))
        .filter(Repository.language.isnot(None))
        .group_by(Repository.language)
        .order_by(func.count(Repository.id).desc())
        .first()
    )
    top_language = top_language_row[0] if top_language_row else None

    recent_runs = (
        db.query(CrawlRun)
        .order_by(CrawlRun.started_at.desc())
        .limit(10)
        .all()
    )

    # 扩展统计
    chinese_repos = db.query(func.count(Repository.id)).filter(
        Repository.is_chinese_owner.is_(True)
    ).scalar() or 0
    efficiency_tools = db.query(func.count(Repository.id)).filter(
        Repository.is_efficiency_tool.is_(True)
    ).scalar() or 0
    interpreted_repos = db.query(func.count(func.distinct(AIInterpretation.repository_id))).scalar() or 0

    return SummaryOut(
        total_repos=total_repos,
        total_runs=total_runs,
        by_period=by_period,
        recent_runs=recent_runs,
        avg_stars=float(avg_stars) if avg_stars else 0.0,
        top_language=top_language,
        chinese_repos=chinese_repos,
        efficiency_tools=efficiency_tools,
        interpreted_repos=interpreted_repos,
    )


@router.get("/languages", response_model=List[LanguageStatOut])
def get_languages(
    limit: int = Query(20, ge=1, le=100),
    period: Optional[str] = Query(None, pattern="^(daily|weekly|monthly)$"),
    db: Session = Depends(get_db),
) -> List[LanguageStatOut]:
    """语言分布 (按仓库数). 可按 period 过滤."""
    if period:
        rows = (
            db.query(
                Repository.language,
                func.count(func.distinct(Snapshot.repository_id)).label("cnt"),
            )
            .join(Snapshot, Snapshot.repository_id == Repository.id)
            .filter(Snapshot.period == period)
            .group_by(Repository.language)
            .order_by(func.count(func.distinct(Snapshot.repository_id)).desc())
            .limit(limit)
            .all()
        )
    else:
        rows = (
            db.query(Repository.language, func.count(Repository.id).label("cnt"))
            .group_by(Repository.language)
            .order_by(func.count(Repository.id).desc())
            .limit(limit)
            .all()
        )
    return [LanguageStatOut(language=lang, count=cnt) for lang, cnt in rows]


@router.get("/top", response_model=List[TopRepoOut])
def get_top_repos(
    period: Optional[str] = Query(None, pattern="^(daily|weekly|monthly)$"),
    limit: int = Query(10, ge=1, le=100),
    metric: str = Query("stars", pattern="^(stars|forks)$"),
    db: Session = Depends(get_db),
) -> List[TopRepoOut]:
    """Top N 仓库. 指定 period 时取最新一天的快照, 按 stars_gained 排序 (trending)."""
    if period:
        if metric == "stars":
            sort_col = Snapshot.stars_gained
        else:
            sort_col = Snapshot.forks_at_snapshot
        latest_date_subq = (
            db.query(
                Snapshot.repository_id.label("rid"),
                func.max(Snapshot.snapshot_date).label("max_date"),
            )
            .filter(Snapshot.period == period)
            .group_by(Snapshot.repository_id)
            .subquery()
        )
        rows = (
            db.query(Repository, sort_col.label("metric_value"))
            .join(Snapshot, Snapshot.repository_id == Repository.id)
            .join(
                latest_date_subq,
                (latest_date_subq.c.rid == Repository.id)
                & (Snapshot.snapshot_date == latest_date_subq.c.max_date),
            )
            .filter(Snapshot.period == period)
            .order_by(sort_col.desc())
            .limit(limit)
            .all()
        )
    else:
        sort_col = Repository.stargazers_count if metric == "stars" else Repository.forks_count
        rows = (
            db.query(Repository, sort_col.label("metric_value"))
            .order_by(sort_col.desc())
            .limit(limit)
            .all()
        )
    repos = [r for r, _ in rows]
    interp_map = _attach_latest_interpretations(db, repos)
    out: List[TopRepoOut] = []
    for r, v in rows:
        ro = RepositoryOut.model_validate(r)
        interp = interp_map.get(r.id)
        if interp:
            ro.latest_interpretation = AIInterpretationOut.model_validate(interp)
        out.append(TopRepoOut(repo=ro, metric_value=int(v)))
    return out


@router.get("/timeline", response_model=List[TimelinePointOut])
def get_timeline(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> List[TimelinePointOut]:
    """每日抓取量时间序列."""
    since = now_cn().date() - timedelta(days=days)
    rows = (
        db.query(
            Snapshot.snapshot_date,
            Snapshot.period,
            func.count(Snapshot.id).label("cnt"),
        )
        .filter(Snapshot.snapshot_date >= since)
        .group_by(Snapshot.snapshot_date, Snapshot.period)
        .order_by(Snapshot.snapshot_date.asc(), Snapshot.period.asc())
        .all()
    )
    return [TimelinePointOut(date=d, period=p, count=c) for d, p, c in rows]


@router.get("/categories", response_model=List[CategoryStatOut])
def get_category_stats(
    period: Optional[str] = Query(None, pattern="^(daily|weekly|monthly)$"),
    db: Session = Depends(get_db),
) -> List[CategoryStatOut]:
    """技术领域分类统计 (仓库数, 总star, 总 stars_gained)."""
    if period:
        latest_date_subq = (
            db.query(
                Snapshot.repository_id.label("rid"),
                func.max(Snapshot.snapshot_date).label("max_date"),
            )
            .filter(Snapshot.period == period)
            .group_by(Snapshot.repository_id)
            .subquery()
        )
        rows = (
            db.query(
                Repository.category,
                func.count(Repository.id).label("cnt"),
                func.coalesce(func.sum(Repository.stargazers_count), 0).label("stars"),
                func.coalesce(func.sum(Snapshot.stars_gained), 0).label("gained"),
            )
            .join(Snapshot, Snapshot.repository_id == Repository.id)
            .join(
                latest_date_subq,
                (latest_date_subq.c.rid == Repository.id)
                & (Snapshot.snapshot_date == latest_date_subq.c.max_date),
            )
            .filter(Snapshot.period == period)
            .group_by(Repository.category)
            .order_by(func.count(Repository.id).desc())
            .all()
        )
    else:
        rows = (
            db.query(
                Repository.category,
                func.count(Repository.id).label("cnt"),
                func.coalesce(func.sum(Repository.stargazers_count), 0).label("stars"),
                literal(0).label("gained"),
            )
            .group_by(Repository.category)
            .order_by(func.count(Repository.id).desc())
            .all()
        )
    return [
        CategoryStatOut(
            category=cat,
            count=cnt,
            total_stars=int(stars),
            total_stars_gained=int(gained),
        )
        for cat, cnt, stars, gained in rows
    ]


@router.get("/radar", response_model=List[RadarPointOut])
def get_radar(
    period: Optional[str] = Query(None, pattern="^(daily|weekly|monthly)$"),
    top_per_category: int = Query(3, ge=1, le=10),
    db: Session = Depends(get_db),
) -> List[RadarPointOut]:
    """技术雷达: 按技术分类聚合, 每类返回 top 仓库. 用于雷达图可视化.

    优化: 一次性查出所有分类的最新快照 + 仓库, 在 Python 侧按分类分组取 top N,
    避免对每个分类重复构建 latest_date_subq (原实现 11 分类 = 11 次相同子查询).
    """
    cat_stats = get_category_stats(period=period, db=db)
    if not cat_stats:
        return []

    # 取所有相关分类的仓库 + 最新快照, 一次性查询
    categories = [cs.category for cs in cat_stats]
    # 构造分类过滤条件: categories 可能包含 None (未分类仓库), 而 SQL 的
    # `category IN (..., NULL)` 永远不会匹配 NULL 行, 会导致"其他/未分类"有数量但
    # top_repos 为空. 这里拆成 IN(非空) + IS NULL 的 OR 组合.
    non_null_cats = [c for c in categories if c is not None]
    has_null_cat = any(c is None for c in categories)
    cat_filter_parts = []
    if non_null_cats:
        cat_filter_parts.append(Repository.category.in_(non_null_cats))
    if has_null_cat:
        cat_filter_parts.append(Repository.category.is_(None))
    cat_filter = or_(*cat_filter_parts) if cat_filter_parts else None

    if period:
        # 用窗口函数对每个 (category) 按 stars_gained 降序排名, 取 top N
        # SQLite 3.25+ 支持 ROW_NUMBER, 这里用相关子查询替代以兼容老版本
        latest_date_subq = (
            db.query(
                Snapshot.repository_id.label("rid"),
                func.max(Snapshot.snapshot_date).label("max_date"),
            )
            .filter(Snapshot.period == period)
            .group_by(Snapshot.repository_id)
            .subquery()
        )
        q = (
            db.query(
                Repository,
                Snapshot.stars_gained.label("mv"),
                Repository.category.label("cat"),
            )
            .join(Snapshot, Snapshot.repository_id == Repository.id)
            .join(
                latest_date_subq,
                (latest_date_subq.c.rid == Repository.id)
                & (Snapshot.snapshot_date == latest_date_subq.c.max_date),
            )
            .filter(Snapshot.period == period)
        )
        if cat_filter is not None:
            q = q.filter(cat_filter)
        rows = q.order_by(Repository.category, Snapshot.stars_gained.desc()).all()
    else:
        q = (
            db.query(
                Repository,
                Repository.stargazers_count.label("mv"),
                Repository.category.label("cat"),
            )
        )
        if cat_filter is not None:
            q = q.filter(cat_filter)
        rows = q.order_by(Repository.category, Repository.stargazers_count.desc()).all()

    # 按 category 分组取 top N (rows 已按 (category, mv desc) 排序)
    top_by_cat: dict = {}
    for r, v, cat in rows:
        bucket = top_by_cat.setdefault(cat, [])
        if len(bucket) < top_per_category:
            bucket.append((r, int(v)))
        else:
            # 已满, 由于 rows 按 cat 分组连续, 后续同 cat 可跳过
            pass

    result = []
    for cs in cat_stats:
        cat = cs.category
        cat_label = cat if cat is not None else "其他"
        top = [
            TopRepoOut(repo=RepositoryOut.model_validate(r), metric_value=v)
            for r, v in top_by_cat.get(cat, [])
        ]
        result.append(RadarPointOut(
            category=cat_label,
            repos=cs.count,
            stars_gained=cs.total_stars_gained,
            top_repos=top,
        ))
    return result


@router.get("/industries", response_model=List[IndustryStatOut])
def get_industry_stats(
    period: Optional[str] = Query(None, pattern="^(daily|weekly|monthly)$"),
    db: Session = Depends(get_db),
) -> List[IndustryStatOut]:
    """效率工具行业分布统计. 可按 period 过滤 (仅统计在该 period 有快照的仓库)."""
    if period:
        # 子查询: 每个 repo 在该 period 的最新快照日期
        latest_date_subq = (
            db.query(
                Snapshot.repository_id.label("rid"),
                func.max(Snapshot.snapshot_date).label("max_date"),
            )
            .filter(Snapshot.period == period)
            .group_by(Snapshot.repository_id)
            .subquery()
        )
        rows = (
            db.query(
                Repository.industry,
                func.count(Repository.id).label("cnt"),
                func.coalesce(func.sum(Repository.stargazers_count), 0).label("stars"),
            )
            .join(Snapshot, Snapshot.repository_id == Repository.id)
            .join(
                latest_date_subq,
                (latest_date_subq.c.rid == Repository.id)
                & (Snapshot.snapshot_date == latest_date_subq.c.max_date),
            )
            .filter(Repository.is_efficiency_tool.is_(True))
            .filter(Snapshot.period == period)
            .group_by(Repository.industry)
            .order_by(func.count(Repository.id).desc())
            .all()
        )
    else:
        rows = (
            db.query(
                Repository.industry,
                func.count(Repository.id).label("cnt"),
                func.coalesce(func.sum(Repository.stargazers_count), 0).label("stars"),
            )
            .filter(Repository.is_efficiency_tool.is_(True))
            .group_by(Repository.industry)
            .order_by(func.count(Repository.id).desc())
            .all()
        )
    return [
        IndustryStatOut(industry=ind, count=cnt, total_stars=int(stars))
        for ind, cnt, stars in rows
    ]
