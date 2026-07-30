"""抓取任务编排 - 调用 GitHub 客户端, upsert 仓库, 写入快照, 更新运行记录.

stars_gained 计算采用快照差值法:
- 查询同 period 的历史快照, stars_gained = 当前总star - 历史快照star
- 无历史快照时 (首次运行), 用仓库年龄估算新增量
此方法不依赖 stargazers API, 不受 token 权限限制.

产品增强:
- 自动分类 (技术领域 + 效率工具行业)
- 中文文档检测
- owner 地区检测 (批量查 user API, 识别国产开源)
- 支持手动取消运行中的抓取任务
"""

from __future__ import annotations

import logging
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import CrawlRun, Repository, Snapshot, SnapshotMonthlySummary, TrendingCache
from ..tz import now_cn, to_cn_datetime
from .classifier import (
    classify_category,
    detect_chinese_doc,
    detect_efficiency_tool,
    detect_region_from_user,
)
from .github import DEFAULT_TOP_LANGUAGES, GitHubSearchClient, compute_since_date

logger = logging.getLogger(__name__)

# 各 period 对应的天数 (用于估算 stars_gained)
PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}

# owner 地区缓存 (login -> (region, is_chinese)), 避免重复查 user API.
# 使用 OrderedDict 实现 LRU, 上限 5000 条, 防止长期运行无限增长.
_REGION_CACHE_MAX = 5000
_region_cache: "OrderedDict[str, Tuple[str, bool]]" = OrderedDict()
_region_cache_lock = threading.Lock()


def _region_cache_get(owner: str) -> Optional[Tuple[str, bool]]:
    with _region_cache_lock:
        val = _region_cache.get(owner)
        if val is not None:
            _region_cache.move_to_end(owner)
        return val


def _region_cache_put(owner: str, value: Tuple[str, bool]) -> None:
    with _region_cache_lock:
        _region_cache[owner] = value
        _region_cache.move_to_end(owner)
        while len(_region_cache) > _REGION_CACHE_MAX:
            _region_cache.popitem(last=False)

# 正在运行的抓取任务的取消事件 (run_id -> threading.Event)
# 主线程设置 event, 抓取线程在循环中检查并提前退出
_active_runs: Dict[int, threading.Event] = {}
# run_id -> period, 用于同 period 并发互斥检查
_active_run_periods: Dict[int, str] = {}
_active_runs_lock = threading.Lock()


def request_crawl_cancel(run_id: int) -> bool:
    """请求取消某个正在运行的抓取任务.

    Returns:
        True 如果任务存在且已发出取消信号; False 如果任务不存在或已结束
    """
    with _active_runs_lock:
        event = _active_runs.get(run_id)
        if event is None:
            return False
        event.set()
        return True


def is_crawl_cancelled(run_id: int) -> bool:
    """检查任务是否已被请求取消."""
    with _active_runs_lock:
        event = _active_runs.get(run_id)
        return event is not None and event.is_set()


def is_period_running(period: str) -> bool:
    """检查指定 period 是否已有抓取任务在运行 (并发互斥)."""
    with _active_runs_lock:
        return period in _active_run_periods.values()


@dataclass
class CrawlRunSummary:
    """抓取运行结果摘要 (避免 DetachedInstanceError, 不持有 ORM 对象)."""

    run_id: int
    period: str
    status: str
    total_repos_found: int
    total_repos_upserted: int
    error_message: Optional[str] = None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """解析 GitHub API 返回的 ISO 时间字符串, 转为东八区 naive datetime."""
    if not value:
        return None
    # GitHub 返回形如 "2026-07-27T08:30:00Z" (UTC)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return to_cn_datetime(dt)
    except (ValueError, TypeError):
        return None


def _extract_license(item: Dict[str, Any]) -> Optional[str]:
    lic = item.get("license")
    if not lic:
        return None
    return lic.get("spdx_id") or lic.get("name")


def _upsert_repository(db: Session, item: Dict[str, Any]) -> Repository:
    """根据 GitHub API 返回的 item upsert 仓库记录. 自动分类 + 中文检测."""
    github_id = item["id"]
    owner_info = item.get("owner") or {}
    repo = db.query(Repository).filter(Repository.github_id == github_id).first()

    now = now_cn()

    # 自动分类 (技术领域 + 效率工具行业)
    category = classify_category(item)
    is_eff, industry = detect_efficiency_tool(item)
    has_cn_doc = detect_chinese_doc(item)

    if repo is None:
        repo = Repository(
            github_id=github_id,
            name=item.get("name", ""),
            full_name=item.get("full_name", ""),
            owner=owner_info.get("login", ""),
            owner_type=owner_info.get("type"),
            html_url=item.get("html_url", ""),
            description=item.get("description"),
            language=item.get("language"),
            topics=item.get("topics") or [],
            license=_extract_license(item),
            stargazers_count=item.get("stargazers_count", 0),
            forks_count=item.get("forks_count", 0),
            watchers_count=item.get("watchers_count", 0),
            open_issues_count=item.get("open_issues_count", 0),
            created_at=_parse_dt(item.get("created_at")),
            updated_at=_parse_dt(item.get("updated_at")),
            pushed_at=_parse_dt(item.get("pushed_at")),
            first_seen_at=now,
            last_seen_at=now,
            category=category,
            is_efficiency_tool=is_eff,
            industry=industry,
            has_chinese_doc=has_cn_doc,
        )
        db.add(repo)
    else:
        repo.name = item.get("name", repo.name)
        repo.full_name = item.get("full_name", repo.full_name)
        repo.owner = owner_info.get("login", repo.owner)
        repo.owner_type = owner_info.get("type", repo.owner_type)
        repo.html_url = item.get("html_url", repo.html_url)
        repo.description = item.get("description", repo.description)
        repo.language = item.get("language", repo.language)
        repo.topics = item.get("topics") or []
        repo.license = _extract_license(item) or repo.license
        repo.stargazers_count = item.get("stargazers_count", repo.stargazers_count)
        repo.forks_count = item.get("forks_count", repo.forks_count)
        repo.watchers_count = item.get("watchers_count", repo.watchers_count)
        repo.open_issues_count = item.get("open_issues_count", repo.open_issues_count)
        repo.updated_at = _parse_dt(item.get("updated_at")) or repo.updated_at
        repo.pushed_at = _parse_dt(item.get("pushed_at")) or repo.pushed_at
        repo.last_seen_at = now
        # 分类字段更新 (保持已有值, 仅在新值非空时覆盖)
        if category:
            repo.category = category
        if is_eff:
            repo.is_efficiency_tool = True
            if industry:
                repo.industry = industry
        if has_cn_doc:
            repo.has_chinese_doc = True

    db.flush()
    return repo


async def _enrich_regions_async(
    client: GitHubSearchClient,
    items: List[Dict[str, Any]],
) -> None:
    """批量查询 owner 的 user 信息, 缓存并写入 _region_cache.

    只查未缓存的 owner. 已缓存的直接复用.
    """
    import httpx
    # 收集需要查询的 owner (去重 + 未缓存)
    owners_to_query = []
    seen = set()
    for item in items:
        owner = (item.get("owner") or {}).get("login", "")
        if owner and owner not in seen and _region_cache_get(owner) is None:
            seen.add(owner)
            owners_to_query.append(owner)

    if not owners_to_query:
        return

    logger.info("Querying user API for %d unique owners (region detection)", len(owners_to_query))
    async with httpx.AsyncClient() as hc:
        for owner in owners_to_query:
            user_data = await client.get_user_info(hc, owner)
            region, is_cn = detect_region_from_user(user_data)
            _region_cache_put(owner, (region, is_cn))


def _apply_region_to_repo(db: Session, repo: Repository) -> None:
    """从缓存读取 owner 地区, 写入 repo.region / is_chinese_owner."""
    cached = _region_cache_get(repo.owner)
    if cached:
        region, is_cn = cached
        repo.region = region
        repo.is_chinese_owner = is_cn


def _add_snapshot(
    db: Session,
    repository: Repository,
    period: str,
    snapshot_date: date,
    crawl_run_id: Optional[int],
    rank_in_period: Optional[int],
    stars_gained: int = 0,
    score: float = 0.0,
) -> None:
    """为仓库插入一条快照 (若 (repo, period, date) 已存在则更新)."""
    existing = (
        db.query(Snapshot)
        .filter(
            Snapshot.repository_id == repository.id,
            Snapshot.period == period,
            Snapshot.snapshot_date == snapshot_date,
        )
        .first()
    )
    if existing is None:
        snap = Snapshot(
            repository_id=repository.id,
            snapshot_date=snapshot_date,
            period=period,
            stars_at_snapshot=repository.stargazers_count,
            forks_at_snapshot=repository.forks_count,
            stars_gained=stars_gained,
            score=score,
            rank_in_period=rank_in_period,
            crawl_run_id=crawl_run_id,
        )
        db.add(snap)
    else:
        existing.stars_at_snapshot = repository.stargazers_count
        existing.forks_at_snapshot = repository.forks_count
        existing.stars_gained = stars_gained
        existing.score = score
        existing.rank_in_period = rank_in_period
        existing.crawl_run_id = crawl_run_id


def _compute_stars_gained(
    db: Session,
    repository: Repository,
    period: str,
    today: date,
    current_stars: int,
) -> tuple:
    """通过快照差值法计算 stars_gained.

    Returns:
        (stars_gained, is_estimated): 新增量 和 是否为估算值
    """
    period_days = PERIOD_DAYS.get(period, 7)

    # 查询同 period 的历史快照: 取 period_days 天前 (或更早) 的最近一条.
    # 关键: 必须查询 snapshot_date <= today - period_days, 而非 "最近一条".
    # 因为 weekly/monthly 每天都写快照, "最近一条" = 昨天, 差值仅为 1 天增量,
    # 而非该 period 真实的 7/30 天增量.
    # daily: <= today-1 (昨天, 行为不变)
    # weekly: <= today-7 (7 天前)
    # monthly: <= today-30 (30 天前)
    baseline_date = today - timedelta(days=period_days)
    prev_snap = (
        db.query(Snapshot)
        .filter(
            Snapshot.repository_id == repository.id,
            Snapshot.period == period,
            Snapshot.snapshot_date <= baseline_date,
        )
        .order_by(Snapshot.snapshot_date.desc())
        .first()
    )

    if prev_snap is not None:
        # 窗口校验: prev_snap 距 today 不得超过 period_days + 2 (容忍 1-2 天抓取空缺)
        gap_days = (today - prev_snap.snapshot_date).days
        if gap_days <= period_days + 2:
            # 精确差值
            gained = max(0, current_stars - (prev_snap.stars_at_snapshot or 0))
            return gained, False
        # 窗口异常 (历史快照太老), 降级估算
        logger.warning(
            "Snapshot window anomaly: repo=%s period=%s gap=%d days (expected ~%d), "
            "falling back to estimation",
            repository.full_name, period, gap_days, period_days,
        )

    # 无合格历史快照 (首次运行或窗口异常), 用仓库年龄 + 活跃度估算
    if not repository.created_at:
        return 0, True

    # 仓库年龄天数
    if isinstance(repository.created_at, datetime):
        created = repository.created_at.date()
    else:
        created = repository.created_at
    repo_age_days = (today - created).days
    if repo_age_days <= 0:
        # 仓库今天创建, 全部 star 都是新增
        return current_stars, True

    # 基础估算: 按平均增长率推算 period 内新增
    base_estimated = current_stars / repo_age_days * period_days

    # 活跃度因子: 基于 pushed_at 判断仓库在 period 内是否活跃.
    # 这样不同 period 的估算值不再线性放大, 排序会有差异 (更贴近 trending 语义:
    # 短期内有 push 的仓库才是真正活跃的 trending, 长期不活跃仓库在短期 period 排名靠后).
    activity_factor = 1.0
    if repository.pushed_at:
        if isinstance(repository.pushed_at, datetime):
            pushed_date = repository.pushed_at.date()
        else:
            pushed_date = repository.pushed_at
        days_since_push = (today - pushed_date).days
        if days_since_push <= 0:
            # 今天有 push, 非常活跃
            activity_factor = 1.3
        elif days_since_push <= period_days:
            # period 内有 push, 正常活跃
            activity_factor = 1.0
        else:
            # period 内无 push, 按时间衰减 (越久没 push, 估算越低)
            activity_factor = max(0.1, period_days / (period_days + days_since_push))

    estimated = int(base_estimated * activity_factor)
    # 上限保护: period 内新增 star 不可能超过仓库总 star 数
    estimated = min(estimated, current_stars)
    return max(0, estimated), True


# 时间衰减系数 λ: e^(-λ×AgeInDays)
# λ=0.001 → 1年仓库因子0.69, 3年0.34, 5年0.16, 10年0.026
SCORE_LAMBDA = 0.001
# 低基数仓库惩罚阈值: total_stars < 此值时引入线性惩罚因子, 抑制刷星/营销号灌水
SMALL_REPO_THRESHOLD = 50
# age 衰减下限: 防止过度压制成熟基础设施项目 (5年→0.3, 10年→0.3 而非 0.026)
AGE_FACTOR_FLOOR = 0.3


def _compute_score(
    repository: Repository,
    stars_gained: int,
    total_stars: int,
    today: date,
) -> float:
    """计算热度评分.

    Score = ΔStars × small_repo_penalty × (1/log10(TotalStars+10)) × age_factor

    - ΔStars: period 内新增 star (主要排序依据)
    - small_repo_penalty: total_stars<50 时线性惩罚 (抑制刷星/营销号灌水),
      10★→0.2, 49★→0.98, >=50★→1.0
    - 1/log10(TotalStars+10): 对高 Star 总量项目做惩罚, 提升低基数黑马权重
    - age_factor: 时间衰减, 鼓励新项目 (λ=0.001), 下限 0.3 防止过度压制成熟项目
    """
    if stars_gained <= 0:
        return 0.0

    # 低基数惩罚: total_stars<50 时线性衰减 (10★→0.2, 49★→0.98)
    if total_stars < SMALL_REPO_THRESHOLD:
        small_repo_penalty = total_stars / SMALL_REPO_THRESHOLD
    else:
        small_repo_penalty = 1.0

    # 对数惩罚: total_stars=100 → 0.5, 1000 → 0.33, 10000 → 0.25, 100000 → 0.2
    log_penalty = 1.0 / math.log10(total_stars + 10)

    # 时间衰减 (下限 0.3: 5年仓库从 0.16 提升到 0.3, 10年从 0.026 提升到 0.3)
    age_days = 0
    if repository.created_at:
        if isinstance(repository.created_at, datetime):
            created = repository.created_at.date()
        else:
            created = repository.created_at
        age_days = max(0, (today - created).days)
    age_factor = max(AGE_FACTOR_FLOOR, math.exp(-SCORE_LAMBDA * age_days))

    return round(stars_gained * small_repo_penalty * log_penalty * age_factor, 4)


# 历史高分池: 刷新近 7 天有快照但本次未抓取的仓库, 兜底"老树开花"
# 窗口 7 天覆盖一个 weekly 周期, 避免爆发仓库因 3 天窗口过短而漏掉
HISTORICAL_POOL_DAYS = 7
HISTORICAL_POOL_TOP_N = 200


def _refresh_historical_pool(
    db: Session,
    client: "GitHubSearchClient",
    period: str,
    today: date,
    snapshot_date: date,
    run_id: int,
    enriched: List[Tuple[Repository, int, float, bool]],
    cancel_event: threading.Event,
) -> List[Tuple[Repository, int, float, bool]]:
    """刷新历史高分池: 近 7 天有快照但本次未抓取的仓库, 重新拉取最新数据.

    兜底"老树开花"场景: 老仓库近期突然爆发, 但不在 created/pushed 的前 1000 条里.
    对这些仓库调用 /repos/{owner}/{repo} 获取最新 star 数, 重新计算 stars_gained + score.

    限制: 最多刷新 Top 200 个 (按历史 score 降序, score 综合了增量+基数+年龄, 比纯
    stars_gained 更稳定), 控制 API 消耗.
    """
    already_crawled_ids = {repo.id for repo, _, _, _ in enriched}
    since_date = today - timedelta(days=HISTORICAL_POOL_DAYS)

    # 查询近 7 天有快照但本次未抓取的仓库, 按历史 score 降序取 Top N
    # (score 综合了增量+基数+年龄, 比纯 stars_gained 更稳定, 避免首次估算值干扰)
    rows = (
        db.query(Snapshot.repository_id, Snapshot.stars_gained)
        .filter(
            Snapshot.snapshot_date >= since_date,
            Snapshot.snapshot_date < today,
            ~Snapshot.repository_id.in_(list(already_crawled_ids)) if already_crawled_ids else True,
        )
        .order_by(Snapshot.score.desc())
        .limit(HISTORICAL_POOL_TOP_N)
        .all()
    )

    if not rows:
        logger.info("Historical pool: no candidates to refresh")
        return enriched

    repo_ids = [r[0] for r in rows]
    repos = db.query(Repository).filter(Repository.id.in_(repo_ids)).all()
    repo_map = {r.id: r for r in repos}

    logger.info(
        "Historical pool: refreshing %d repos (period=%s)", len(rows), period,
    )

    import asyncio
    import httpx

    async def _refresh_one(hc: httpx.AsyncClient, repo: Repository) -> Optional[Tuple[Repository, int, float, bool]]:
        if cancel_event.is_set():
            return None
        try:
            item = await client.get_repo_info(hc, repo.owner, repo.name)
            if not item or "id" not in item:
                return None
            # upsert (更新最新 star 数等)
            updated_repo = _upsert_repository(db, item)
            current_stars = item.get("stargazers_count", 0)
            gained, is_estimated = _compute_stars_gained(
                db, updated_repo, period, today, current_stars
            )
            sc = _compute_score(updated_repo, gained, current_stars, today)
            _add_snapshot(
                db, updated_repo, period=period, snapshot_date=snapshot_date,
                crawl_run_id=run_id, rank_in_period=None,
                stars_gained=gained, score=sc,
            )
            db.commit()
            return (updated_repo, gained, sc, is_estimated)
        except Exception as e:
            logger.exception("Historical pool refresh failed for %s: %s", repo.full_name, e)
            db.rollback()
            return None

    async def _run_refresh():
        results = []
        async with httpx.AsyncClient() as hc:
            for repo_id, _old_gained in rows:
                if cancel_event.is_set():
                    break
                repo = repo_map.get(repo_id)
                if not repo:
                    continue
                result = await _refresh_one(hc, repo)
                if result:
                    results.append(result)
        return results

    refreshed = asyncio.run(_run_refresh())
    enriched.extend(refreshed)

    logger.info(
        "Historical pool: refreshed %d repos (total enriched now %d)",
        len(refreshed), len(enriched),
    )
    return enriched


def _rebuild_trending_cache(
    db: Session,
    period: str,
    snapshot_date: date,
    trending: List[Tuple[Repository, int, float, bool]],
) -> None:
    """预计算 trending_cache: 按 score 排序写入缓存, 供 /api/v1/trending 高性能读取.

    写入两份缓存:
    1. language="all": 全部 trending 仓库
    2. 按 DEFAULT_TOP_LANGUAGES 分语言: 每个 language 一份缓存 (language 字段存小写)
       覆盖热门语言, 避免按语言查询走实时 JOIN.
    """
    from ..config import settings

    # 决定要预缓存的语言列表 (小写)
    top_n = settings.GITHUB_CRAWL_TOP_LANGUAGES
    cache_langs = {lang.lower() for lang in DEFAULT_TOP_LANGUAGES[:top_n]} if top_n > 0 else set()

    # 清除该 period 的旧缓存 (all + 各语言)
    db.query(TrendingCache).filter(
        TrendingCache.time_window == period,
    ).delete()

    now = now_cn()

    # 1. 写入 language="all" 缓存
    for rank, (repo, gained, sc, _est) in enumerate(trending, start=1):
        db.add(TrendingCache(
            time_window=period,
            language="all",
            repository_id=repo.id,
            rank=rank,
            delta_stars=gained,
            score=sc,
            calculated_at=now,
        ))

    # 2. 按语言分组写入 (只对 cache_langs 中的语言)
    # 按 repo.language.lower() 分组, 每组按 score 降序赋 rank
    lang_groups: Dict[str, List[Tuple[Repository, int, float]]] = {}
    for repo, gained, sc, _est in trending:
        lang = (repo.language or "").lower()
        if lang and lang in cache_langs:
            lang_groups.setdefault(lang, []).append((repo, gained, sc))

    for lang, group in lang_groups.items():
        # 组内已按 score 降序 (trending 本身按 score 降序), 但分组后仍需重排以保证连续
        group.sort(key=lambda x: x[2], reverse=True)
        for rank, (repo, gained, sc) in enumerate(group, start=1):
            db.add(TrendingCache(
                time_window=period,
                language=lang,
                repository_id=repo.id,
                rank=rank,
                delta_stars=gained,
                score=sc,
                calculated_at=now,
            ))

    db.commit()
    lang_counts = {lang: len(g) for lang, g in lang_groups.items()}
    logger.info(
        "Trending cache rebuilt: period=%s all=%d langs=%s",
        period, len(trending), lang_counts,
    )


def archive_old_snapshots(retention_days: Optional[int] = None) -> dict:
    """归档超期快照: 将超过保留期的明细快照聚合成月度摘要后删除.

    流程:
    1. 查询 snapshot_date < today - retention_days 的快照
    2. 按 (repository_id, period, year, month) 聚合:
       - month_end_stars: 该月最后一次快照的 stars_at_snapshot
       - max_stars_gained: 该月 stars_gained 最大值
       - max_score: 该月 score 最大值
       - snapshot_count: 该月快照条数
    3. upsert 到 snapshot_monthly_summary (同月已存在则更新)
    4. 删除已归档的明细快照

    Args:
        retention_days: 保留天数, 默认从 settings.SNAPSHOT_RETENTION_DAYS 读取

    Returns:
        归档统计: {archived_rows, summary_upserted, cutoff_date}
    """
    from ..config import settings

    if retention_days is None:
        retention_days = settings.SNAPSHOT_RETENTION_DAYS

    today = now_cn().date()
    cutoff_date = today - timedelta(days=retention_days)
    db = SessionLocal()

    try:
        # 查询待归档的快照 (按 repository_id, period, year, month 分组聚合)
        # SQLite 支持 strftime 提取年月
        from sqlalchemy import func as _func

        agg_rows = (
            db.query(
                Snapshot.repository_id.label("rid"),
                Snapshot.period.label("period"),
                _func.strftime("%Y", Snapshot.snapshot_date).label("yr"),
                _func.strftime("%m", Snapshot.snapshot_date).label("mo"),
                _func.count(Snapshot.id).label("cnt"),
                _func.max(Snapshot.stars_gained).label("max_gained"),
                _func.max(Snapshot.score).label("max_score"),
            )
            .filter(Snapshot.snapshot_date < cutoff_date)
            .group_by(
                Snapshot.repository_id,
                Snapshot.period,
                _func.strftime("%Y", Snapshot.snapshot_date),
                _func.strftime("%m", Snapshot.snapshot_date),
            )
            .all()
        )

        if not agg_rows:
            logger.info("Archive: no snapshots older than %s to archive", cutoff_date)
            return {"archived_rows": 0, "summary_upserted": 0, "cutoff_date": str(cutoff_date)}

        # 收集每个 (rid, period, year, month) 的月末 stars (该月最后一条快照的 stars_at_snapshot)
        # 通过单独查询获取, 避免复杂子查询
        summary_upserted = 0
        for row in agg_rows:
            year = int(row.yr)
            month = int(row.mo)
            # 查该月最后一条快照的 stars_at_snapshot
            last_snap = (
                db.query(Snapshot.stars_at_snapshot)
                .filter(
                    Snapshot.repository_id == row.rid,
                    Snapshot.period == row.period,
                    _func.strftime("%Y", Snapshot.snapshot_date) == row.yr,
                    _func.strftime("%m", Snapshot.snapshot_date) == row.mo,
                    Snapshot.snapshot_date < cutoff_date,
                )
                .order_by(Snapshot.snapshot_date.desc())
                .first()
            )
            month_end_stars = last_snap[0] if last_snap else 0

            # upsert 月度摘要 (同月已存在则更新)
            existing = (
                db.query(SnapshotMonthlySummary)
                .filter(
                    SnapshotMonthlySummary.repository_id == row.rid,
                    SnapshotMonthlySummary.period == row.period,
                    SnapshotMonthlySummary.year == year,
                    SnapshotMonthlySummary.month == month,
                )
                .first()
            )
            if existing:
                existing.month_end_stars = month_end_stars
                existing.max_stars_gained = row.max_gained or 0
                existing.max_score = row.max_score or 0.0
                existing.snapshot_count = row.cnt
            else:
                db.add(SnapshotMonthlySummary(
                    repository_id=row.rid,
                    period=row.period,
                    year=year,
                    month=month,
                    month_end_stars=month_end_stars,
                    max_stars_gained=row.max_gained or 0,
                    max_score=row.max_score or 0.0,
                    snapshot_count=row.cnt,
                ))
            summary_upserted += 1

        db.commit()

        # 删除已归档的明细快照
        deleted = (
            db.query(Snapshot)
            .filter(Snapshot.snapshot_date < cutoff_date)
            .delete(synchronize_session=False)
        )
        db.commit()

        logger.info(
            "Archive done: cutoff=%s archived_rows=%d summary_upserted=%d",
            cutoff_date, deleted, summary_upserted,
        )
        return {
            "archived_rows": deleted,
            "summary_upserted": summary_upserted,
            "cutoff_date": str(cutoff_date),
        }
    except Exception as e:
        db.rollback()
        logger.exception("Archive failed: %s", e)
        raise
    finally:
        db.close()


def crawl_period(
    period: str,
    threshold: Optional[int] = None,
    languages: Optional[List[str]] = None,
    today: Optional[date] = None,
    write_snapshot: bool = True,
) -> CrawlRunSummary:
    """抓取一个 period 的候选仓库并入库, 用快照差值法计算 stars_gained.

    流程:
    1. Search API 获取候选仓库 (总 star > threshold)
    2. upsert 所有候选仓库
    3. 用快照差值法计算每个候选的 stars_gained
    4. 所有候选写入快照 (建立下次差值基准)
    5. trending 榜单 = stars_gained > threshold 的候选, 按 stars_gained 降序排名

    Args:
        period: daily / weekly / monthly
        threshold: star 新增阈值, 默认从 settings 读取
        languages: 显式指定语言列表, None 则按 settings 配置
        today: 用于测试注入日期
        write_snapshot: 是否写快照. False 时仅刷新 Repository 表 (用于 daily 12:00 刷新,
            避免覆盖 00:00 的差值基准), 跳过 stars_gained/score/快照/排名/缓存.

    Returns:
        CrawlRunSummary 摘要对象 (不持有 ORM 引用, 可安全传递)
    """
    from ..config import settings

    threshold = threshold if threshold is not None else settings.GITHUB_CRAWL_THRESHOLD
    today = today or now_cn().date()
    snapshot_date = today

    db = SessionLocal()
    run = CrawlRun(
        started_at=now_cn(),
        period=period,
        status="running",
        params={
            "threshold": threshold,
            "languages": languages,
            "top_languages_setting": settings.GITHUB_CRAWL_TOP_LANGUAGES,
            "write_snapshot": write_snapshot,
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = run.id

    # 注册取消事件
    cancel_event = threading.Event()
    with _active_runs_lock:
        _active_runs[run_id] = cancel_event
        _active_run_periods[run_id] = period

    logger.info("Crawl run #%d started: period=%s threshold=%d", run_id, period, threshold)

    try:
        # 流式抓取: 边搜索边入库, 实现实时进度 + 抓一条入库一条
        client = GitHubSearchClient()
        import asyncio
        import httpx

        async def _stream_crawl_and_store():
            """流式抓取 + 逐条入库.

            每从 GitHub Search API 收到一个 item, 立即:
            1. upsert 仓库
            2. 查询 owner 地区 (带缓存)
            3. 计算 stars_gained (快照差值法)
            4. 写快照 (rank=None, 待全部完成后回填)
            5. 更新 CrawlRun 进度并 commit
            """
            enriched = []  # [(repo, gained, is_estimated)] 用于后续排名
            processed = 0

            async with httpx.AsyncClient() as hc:
                async for item in client.crawl_period_iter(
                    period=period,
                    threshold=threshold,
                    languages=languages,
                    today=today,
                    cancel_check=cancel_event.is_set,
                ):
                    # 取消检查 (双保险: crawl_period_iter 内部也检查)
                    if cancel_event.is_set():
                        logger.info("Crawl run #%d cancelled at item #%d", run_id, processed + 1)
                        raise RuntimeError("用户手动停止抓取")

                    try:
                        # 1. upsert 仓库
                        repo = _upsert_repository(db, item)

                        # 2. 查询 owner 地区 (带缓存, 避免重复查 user API)
                        owner = (item.get("owner") or {}).get("login", "")
                        if owner and _region_cache_get(owner) is None:
                            user_data = await client.get_user_info(hc, owner)
                            region, is_cn = detect_region_from_user(user_data)
                            _region_cache_put(owner, (region, is_cn))
                        _apply_region_to_repo(db, repo)

                        if write_snapshot:
                            # 3. 计算 stars_gained
                            current_stars = item.get("stargazers_count", 0)
                            # 优先使用 Trending 页面提供的真实增量 (非估算)
                            page_gained = item.pop("_stars_gained_from_page", None)
                            if page_gained is not None and page_gained > 0:
                                gained = page_gained
                                is_estimated = False
                            else:
                                gained, is_estimated = _compute_stars_gained(
                                    db, repo, period, today, current_stars
                                )

                            # 3.5 计算热度评分 (Score)
                            sc = _compute_score(repo, gained, current_stars, today)

                            # 4. 写快照 (rank=None, 全部完成后回填)
                            _add_snapshot(
                                db,
                                repo,
                                period=period,
                                snapshot_date=snapshot_date,
                                crawl_run_id=run_id,
                                rank_in_period=None,
                                stars_gained=gained,
                                score=sc,
                            )

                            db.commit()

                            enriched.append((repo, gained, sc, is_estimated))

                        # 5. 更新 CrawlRun 进度 (前端可见实时进度)
                        run.total_repos_found = processed + 1
                        run.total_repos_upserted = processed + 1
                        db.commit()

                        processed += 1

                        if processed % 10 == 0:
                            logger.info(
                                "Crawl run #%d progress: %d repos stored",
                                run_id, processed,
                            )
                    except Exception as e:
                        logger.exception(
                            "Failed to process %s: %s",
                            item.get("full_name"), e,
                        )
                        db.rollback()
                        continue

            logger.info(
                "Crawl run #%d streaming done: %d repos stored",
                run_id, processed,
            )
            return enriched

        enriched = asyncio.run(_stream_crawl_and_store())

        if not write_snapshot:
            # 刷新模式 (daily 12:00): 跳过历史池/排名/缓存, 直接收尾
            logger.info(
                "Crawl run #%d refresh-only done: %d repos updated (no snapshot written)",
                run_id, len(enriched),
            )
            run = db.query(CrawlRun).get(run_id)
            run.finished_at = now_cn()
            run.status = "success"
            run.total_repos_found = len(enriched)
            run.total_repos_upserted = len(enriched)
            db.commit()
            return CrawlRunSummary(
                run_id=run_id,
                period=period,
                status="success",
                total_repos_found=len(enriched),
                total_repos_upserted=len(enriched),
            )

        # ===== Phase 3: 历史高分池 (近 3 天有快照但本次未抓取的仓库) =====
        # 兜底"老树开花": 老仓库近期突然爆发, 但不在 created/pushed 的前 1000 条里
        enriched = _refresh_historical_pool(
            db, client, period, today, snapshot_date, run_id, enriched, cancel_event
        )

        # 全部入库完成, 现在按 score 计算 trending 排名并回填 rank_in_period
        trending = [
            (repo, gained, sc, est)
            for repo, gained, sc, est in enriched
            if gained > threshold
        ]
        # 按 score 降序排名 (而非 stars_gained, 防止老牌大项目霸榜)
        trending.sort(key=lambda x: x[2], reverse=True)

        upserted = 0
        for rank, (repo, gained, sc, est) in enumerate(trending, start=1):
            if cancel_event.is_set():
                logger.info("Crawl run #%d cancelled during rank backfill", run_id)
                raise RuntimeError("用户手动停止抓取")
            try:
                # 回填 rank 到快照
                snap = (
                    db.query(Snapshot)
                    .filter(
                        Snapshot.repository_id == repo.id,
                        Snapshot.period == period,
                        Snapshot.snapshot_date == snapshot_date,
                    )
                    .first()
                )
                if snap:
                    snap.rank_in_period = rank
                    upserted += 1
            except Exception as e:
                logger.exception("Failed to backfill rank for %s: %s", repo.full_name, e)
                db.rollback()
                continue

        db.commit()

        # ===== 预计算 trending_cache (供 /api/v1/trending 高性能读取) =====
        _rebuild_trending_cache(db, period, snapshot_date, trending)

        # 更新 crawl_run 最终状态
        # total_repos_found / total_repos_upserted 使用 len(enriched) (所有抓取并写入快照的仓库数),
        # 与 /api/stats/timeline 统计的 Snapshot 行数保持一致.
        # (trending 数 = 入选榜单的仓库数, 体现在 trending_cache 中, 不再写入这两个字段,
        #  否则会出现"时间线 1378 条 vs 抓取记录发现 200 条"的不一致.)
        run = db.query(CrawlRun).get(run_id)
        run.finished_at = now_cn()
        run.status = "success"
        run.total_repos_found = len(enriched)
        run.total_repos_upserted = len(enriched)
        db.commit()

        est_count = sum(1 for _, _, _, est in enriched if est)
        logger.info(
            "Crawl run #%d succeeded: candidates=%d trending=%d upserted=%d (estimated=%d)",
            run_id, len(enriched), len(trending), upserted, est_count,
        )
        return CrawlRunSummary(
            run_id=run_id,
            period=period,
            status="success",
            total_repos_found=len(enriched),
            total_repos_upserted=len(enriched),
        )

    except Exception as e:
        err_msg = str(e)[:2000]
        is_cancelled = cancel_event.is_set()
        logger.exception("Crawl run #%d failed: %s", run_id, e)
        db.rollback()
        run = db.query(CrawlRun).get(run_id)
        if run is not None:
            run.finished_at = now_cn()
            # 取消视为 cancelled 状态 (前端可区分), 其他异常为 failed
            run.status = "cancelled" if is_cancelled else "failed"
            run.error_message = err_msg
            db.commit()
        return CrawlRunSummary(
            run_id=run_id,
            period=period,
            status="cancelled" if is_cancelled else "failed",
            total_repos_found=0,
            total_repos_upserted=0,
            error_message=err_msg,
        )
    finally:
        # 注销取消事件
        with _active_runs_lock:
            _active_runs.pop(run_id, None)
            _active_run_periods.pop(run_id, None)
        db.close()
