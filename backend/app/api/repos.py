"""/api/repos 端点 - 仓库列表, 详情, 快照."""

from __future__ import annotations

import base64
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, desc, func, or_
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import AIInterpretation, Repository, Snapshot
from ..schemas import (
    AIInterpretationOut,
    RepositoryAggregates,
    RepositoryDetailOut,
    RepositoryListOut,
    RepositoryOut,
    SnapshotOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repos", tags=["repos"])


class TTLCache:
    """进程内 TTL + LRU 缓存 (R2: 防止无界 dict 导致 OOM).

    - 每个条目带 TTL, 过期惰性淘汰.
    - 容量上限, 超出按 LRU 淘汰.
    - 线程安全.
    """

    def __init__(self, maxsize: int, ttl: float) -> None:
        self.maxsize = maxsize
        self.ttl = ttl
        self._store: "OrderedDict[Any, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, val = entry
            if time.time() - ts > self.ttl:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return val

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.time(), value)
            self._store.move_to_end(key)
            while len(self._store) > self.maxsize:
                self._store.popitem(last=False)

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# README 缓存: repo_id -> content. TTL 5 分钟, 上限 500 条 (R2).
README_CACHE_TTL = 300  # 秒
_README_CACHE = TTLCache(maxsize=500, ttl=README_CACHE_TTL)
# 翻译缓存: repo_id -> translated_content. TTL 7 天, 上限 200 条 (R2: 原实现无 TTL 永久缓存).
_TRANSLATE_CACHE = TTLCache(maxsize=200, ttl=7 * 24 * 3600)

# 允许排序的字段映射 (gained 为虚拟字段, 由 list_repos 特殊处理)
SORT_FIELDS = {
    "stars": Repository.stargazers_count,
    "forks": Repository.forks_count,
    "updated": Repository.updated_at,
    "created": Repository.created_at,
    "pushed": Repository.pushed_at,
    "name": Repository.name,
}


def _parse_favorite_ids(raw: Optional[str]) -> Optional[List[int]]:
    """解析逗号分隔的仓库 ID 列表 (前端"仅看收藏"传入)."""
    if not raw:
        return None
    try:
        ids = [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        return []
    return ids


def _apply_favorite_filter(query, fav_ids: Optional[List[int]]):
    """按收藏 ID 列表过滤. 无有效 ID 时返回空集 (前端无收藏)."""
    if fav_ids is None:
        return query
    if not fav_ids:
        # 无收藏: 返回空结果 (id 恒为正, < 0 永不命中)
        return query.filter(Repository.id < 0)
    return query.filter(Repository.id.in_(fav_ids))


def _apply_filters(query, params: dict, skip_period: bool = False):
    """应用筛选条件.

    O1: 合并原 _apply_filters 与 _apply_filters_non_period 为单函数.
    skip_period=True 时跳过 period 的 exists 过滤 (用于 sort=gained 路径, period 已通过 join 体现).
    """
    period = params.get("period")
    language = params.get("language")
    min_stars = params.get("min_stars")
    max_stars = params.get("max_stars")
    topic = params.get("topic")
    license_ = params.get("license")
    q = params.get("q")
    region = params.get("region")
    category = params.get("category")
    is_chinese_doc = params.get("is_chinese_doc")
    is_efficiency = params.get("is_efficiency_tool")
    industry = params.get("industry")

    if language:
        query = query.filter(Repository.language == language)
    if min_stars is not None:
        query = query.filter(Repository.stargazers_count >= min_stars)
    if max_stars is not None:
        query = query.filter(Repository.stargazers_count <= max_stars)
    if license_:
        query = query.filter(Repository.license == license_)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Repository.name.ilike(like), Repository.full_name.ilike(like), Repository.description.ilike(like))
        )
    if topic:
        query = query.filter(Repository.topics.like(f'%"{topic}"%'))
    # 产品扩展筛选
    if region:
        if region == "china":
            query = query.filter(Repository.is_chinese_owner.is_(True))
        elif region == "overseas":
            query = query.filter(Repository.region == "overseas")
    if category:
        query = query.filter(Repository.category == category)
    if is_chinese_doc:
        query = query.filter(Repository.has_chinese_doc.is_(True))
    if is_efficiency:
        query = query.filter(Repository.is_efficiency_tool.is_(True))
    if industry:
        query = query.filter(Repository.industry == industry)
    # 收藏 ID 过滤 (前端"仅看收藏")
    query = _apply_favorite_filter(query, params.get("favorite_ids"))

    # period 通过 snapshots 过滤 (sort=gained 路径跳过, period 已通过 join 体现)
    if not skip_period:
        if period and period != "all":
            from sqlalchemy import exists

            snapshot_date_filter = params.get("snapshot_date")
            cond = (Snapshot.repository_id == Repository.id) & (Snapshot.period == period)
            if snapshot_date_filter:
                cond = cond & (Snapshot.snapshot_date == snapshot_date_filter)
            query = query.filter(exists().where(cond))
        elif period == "all":
            # period=all: 只返回在任意 period (daily/weekly/monthly) 有快照的仓库 (聚合去重)
            from sqlalchemy import exists

            cond = Snapshot.repository_id == Repository.id
            query = query.filter(exists().where(cond))

    return query


def _attach_stars_gained(db: Session, repos: List[Repository], period: Optional[str]) -> dict:
    """批量查询每个 repo 在指定 period 最新一天的 stars_gained.

    Returns:
        {repo_id: (stars_gained, is_estimated)} - 增量与是否估算值标记.
    """
    if not repos or not period or period == "all":
        return {}
    repo_ids = [r.id for r in repos]
    # 子查询: 每个 repo 在该 period 的最新快照日期
    latest_date_subq = (
        db.query(
            Snapshot.repository_id.label("rid"),
            func.max(Snapshot.snapshot_date).label("max_date"),
        )
        .filter(Snapshot.period == period, Snapshot.repository_id.in_(repo_ids))
        .group_by(Snapshot.repository_id)
        .subquery()
    )
    rows = (
        db.query(Snapshot.repository_id, Snapshot.stars_gained, Snapshot.is_estimated)
        .join(
            latest_date_subq,
            (latest_date_subq.c.rid == Snapshot.repository_id)
            & (latest_date_subq.c.max_date == Snapshot.snapshot_date),
        )
        .filter(Snapshot.period == period)
        .all()
    )
    return {rid: (gained, bool(est)) for rid, gained, est in rows}


def _attach_latest_interpretations(db: Session, repos: List[Repository]) -> dict:
    """批量查询每个 repo 的最新 AI 解读. 返回 {repo_id: AIInterpretation}."""
    if not repos:
        return {}
    repo_ids = [r.id for r in repos]
    # 子查询: 每个 repo 的最新解读 generated_at
    latest_subq = (
        db.query(
            AIInterpretation.repository_id.label("rid"),
            func.max(AIInterpretation.generated_at).label("max_ts"),
        )
        .filter(AIInterpretation.repository_id.in_(repo_ids))
        .group_by(AIInterpretation.repository_id)
        .subquery()
    )
    rows = (
        db.query(AIInterpretation)
        .join(
            latest_subq,
            (latest_subq.c.rid == AIInterpretation.repository_id)
            & (latest_subq.c.max_ts == AIInterpretation.generated_at),
        )
        .all()
    )
    return {i.repository_id: i for i in rows}


def _compute_aggregates(
    db: Session,
    filtered_query,
    period: Optional[str],
    has_snapshot_join: bool = False,
) -> RepositoryAggregates:
    """计算当前筛选条件下的全量聚合统计 (跨所有分页).

    filtered_query: 已应用筛选条件但未分页的查询
    has_snapshot_join: True 表示查询已 join Snapshot (sort=gained 路径),
                       可直接对 Snapshot.stars_gained 求和
    """
    # 总 star / 总 forks (Repository 为主查询实体, 两种路径均可直接聚合)
    stars_forks = filtered_query.with_entities(
        func.coalesce(func.sum(Repository.stargazers_count), 0),
        func.coalesce(func.sum(Repository.forks_count), 0),
    ).one()
    total_stars = int(stars_forks[0] or 0)
    total_forks = int(stars_forks[1] or 0)

    # 国产仓库数
    chinese_count = filtered_query.filter(
        Repository.is_chinese_owner.is_(True)
    ).count()

    # 已解读仓库数: 在筛选集合中存在 AI 解读记录的仓库
    interp_subq = db.query(AIInterpretation.repository_id).distinct().subquery()
    interpreted_count = filtered_query.filter(
        Repository.id.in_(interp_subq)
    ).count()

    # 周期新增 star 总和 (仅 period != all 时有意义)
    total_gained = 0
    if period and period != "all":
        if has_snapshot_join:
            # sort=gained 路径: Snapshot 已 join, 每个 repo 仅一行最新快照
            total_gained = int(
                filtered_query.with_entities(
                    func.coalesce(func.sum(Snapshot.stars_gained), 0)
                ).scalar() or 0
            )
        else:
            # 常规路径: 需单独 join 快照求和
            latest_date_subq = (
                db.query(
                    Snapshot.repository_id.label("rid"),
                    func.max(Snapshot.snapshot_date).label("max_date"),
                )
                .filter(Snapshot.period == period)
                .group_by(Snapshot.repository_id)
                .subquery()
            )
            repo_ids_subq = filtered_query.with_entities(Repository.id).subquery()
            total_gained = int(
                db.query(func.coalesce(func.sum(Snapshot.stars_gained), 0))
                .join(
                    latest_date_subq,
                    (latest_date_subq.c.rid == Snapshot.repository_id)
                    & (Snapshot.snapshot_date == latest_date_subq.c.max_date),
                )
                .filter(Snapshot.period == period)
                .filter(Snapshot.repository_id.in_(repo_ids_subq))
                .scalar() or 0
            )

    return RepositoryAggregates(
        total_stars=total_stars,
        total_forks=total_forks,
        total_gained=total_gained,
        chinese_count=chinese_count,
        interpreted_count=interpreted_count,
    )


@router.get("", response_model=RepositoryListOut)
def list_repos(
    period: Optional[str] = Query(None, pattern="^(daily|weekly|monthly|all)$"),
    language: Optional[str] = None,
    min_stars: Optional[int] = None,
    max_stars: Optional[int] = None,
    topic: Optional[str] = None,
    license: Optional[str] = None,
    q: Optional[str] = None,
    sort: str = Query("stars", pattern="^(stars|forks|updated|created|pushed|name|gained)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    # 产品扩展筛选
    region: Optional[str] = Query(None, pattern="^(china|overseas|unknown)$"),
    category: Optional[str] = None,
    is_chinese_doc: Optional[bool] = None,
    is_efficiency_tool: Optional[bool] = None,
    industry: Optional[str] = None,
    favorite_ids: Optional[str] = Query(
        None, description="逗号分隔的仓库 ID, 仅返回这些仓库 (前端'仅看收藏'用)"
    ),
    db: Session = Depends(get_db),
) -> RepositoryListOut:
    """仓库列表 (分页 + 多维筛选).

    - sort=gained 时按 period 最新快照的 stars_gained 排序 (需指定 period)
    - 返回 items 中包含 stars_gained (period != all 时) 和 latest_interpretation
    - favorite_ids 传入时仅在这些仓库中筛选 (支持跨页"仅看收藏")
    """
    params = {
        "period": period,
        "language": language,
        "min_stars": min_stars,
        "max_stars": max_stars,
        "topic": topic,
        "license": license,
        "q": q,
        "region": region,
        "category": category,
        "is_chinese_doc": is_chinese_doc,
        "is_efficiency_tool": is_efficiency_tool,
        "industry": industry,
        "favorite_ids": _parse_favorite_ids(favorite_ids),
    }

    # ====== sort=gained 特殊路径: 按 Snapshot.stars_gained 排序 ======
    if sort == "gained":
        if not period or period == "all":
            # 无 period 时降级为 stars 排序
            sort_field = Repository.stargazers_count
            query = db.query(Repository)
            query = _apply_filters(query, params)
            total = query.count()
            query = query.order_by(desc(sort_field) if order == "desc" else asc(sort_field))
            items = query.offset((page - 1) * per_page).limit(per_page).all()
        else:
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
            # 主查询: Repository join 最新快照, 按 stars_gained 排序
            base = (
                db.query(
                    Repository,
                    Snapshot.stars_gained.label("gained_val"),
                    Snapshot.is_estimated.label("est_val"),
                )
                .join(Snapshot, Snapshot.repository_id == Repository.id)
                .join(
                    latest_date_subq,
                    (latest_date_subq.c.rid == Repository.id)
                    & (latest_date_subq.c.max_date == Snapshot.snapshot_date),
                )
                .filter(Snapshot.period == period)
            )
            # 应用过滤 (period 已通过 join 体现, 不再用 exists)
            base = _apply_filters(base, params, skip_period=True)
            total = base.count()
            # 聚合统计 (跨所有分页, Snapshot 已 join)
            aggregates = _compute_aggregates(db, base, period, has_snapshot_join=True)
            # 按 stars_gained 排序 (用户期望按新增 star 数排序)
            sort_col = Snapshot.stars_gained
            base = base.order_by(desc(sort_col) if order == "desc" else asc(sort_col))
            rows = base.offset((page - 1) * per_page).limit(per_page).all()
            items = [r for r, _, _ in rows]
            # 构造 {repo_id: (stars_gained, is_estimated)} 映射
            gained_map = {r.id: (int(g), bool(est)) for r, g, est in rows}
            interp_map = _attach_latest_interpretations(db, items)
            out_items = []
            for r in items:
                ro = RepositoryOut.model_validate(r)
                gained, est = gained_map.get(r.id, (None, None))
                ro.stars_gained = gained
                ro.stars_gained_is_estimated = est
                interp = interp_map.get(r.id)
                if interp:
                    ro.latest_interpretation = AIInterpretationOut.model_validate(interp)
                out_items.append(ro)
            return RepositoryListOut(
                items=out_items, total=total, page=page, per_page=per_page,
                aggregates=aggregates,
            )

    # ====== 常规排序路径 ======
    query = db.query(Repository)
    query = _apply_filters(query, params)

    total = query.count()
    # 聚合统计 (跨所有分页)
    aggregates = _compute_aggregates(db, query, period, has_snapshot_join=False)

    sort_field = SORT_FIELDS.get(sort, Repository.stargazers_count)
    query = query.order_by(desc(sort_field) if order == "desc" else asc(sort_field))

    items = query.offset((page - 1) * per_page).limit(per_page).all()

    # 批量附加 stars_gained (period != all 时) 和 latest_interpretation
    gained_map = _attach_stars_gained(db, items, period)
    interp_map = _attach_latest_interpretations(db, items)

    out_items = []
    for r in items:
        ro = RepositoryOut.model_validate(r)
        if r.id in gained_map:
            gained, est = gained_map[r.id]
            ro.stars_gained = gained
            ro.stars_gained_is_estimated = est
        interp = interp_map.get(r.id)
        if interp:
            ro.latest_interpretation = AIInterpretationOut.model_validate(interp)
        out_items.append(ro)

    return RepositoryListOut(
        items=out_items,
        total=total,
        page=page,
        per_page=per_page,
        aggregates=aggregates,
    )


@router.get("/{repo_id}", response_model=RepositoryDetailOut)
def get_repo(repo_id: int, db: Session = Depends(get_db)) -> RepositoryDetailOut:
    """仓库详情 (含全部快照和 AI 解读)."""
    repo = db.query(Repository).get(repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    snapshots = (
        db.query(Snapshot)
        .filter(Snapshot.repository_id == repo_id)
        .order_by(Snapshot.snapshot_date.asc(), Snapshot.period.asc())
        .all()
    )
    interpretations = (
        db.query(AIInterpretation)
        .filter(AIInterpretation.repository_id == repo_id)
        .order_by(AIInterpretation.generated_at.desc())
        .all()
    )
    out = RepositoryDetailOut.model_validate(repo)
    out.snapshots = [SnapshotOut.model_validate(s) for s in snapshots]
    out.interpretations = [AIInterpretationOut.model_validate(i) for i in interpretations]
    return out


@router.get("/{repo_id}/snapshots", response_model=List[SnapshotOut])
def list_repo_snapshots(repo_id: int, db: Session = Depends(get_db)) -> List[SnapshotOut]:
    """获取仓库所有快照 (用于绘制 star 趋势)."""
    repo = db.query(Repository).get(repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    snaps = (
        db.query(Snapshot)
        .filter(Snapshot.repository_id == repo_id)
        .order_by(Snapshot.snapshot_date.asc(), Snapshot.period.asc())
        .all()
    )
    return [SnapshotOut.model_validate(s) for s in snaps]


@router.get("/{repo_id}/interpretation", response_model=List[AIInterpretationOut])
def list_repo_interpretations(repo_id: int, db: Session = Depends(get_db)) -> List[AIInterpretationOut]:
    """获取仓库的 AI 解读."""
    repo = db.query(Repository).get(repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    interps = (
        db.query(AIInterpretation)
        .filter(AIInterpretation.repository_id == repo_id)
        .order_by(AIInterpretation.generated_at.desc())
        .all()
    )
    return [AIInterpretationOut.model_validate(i) for i in interps]


# 中文 README 常见文件名, 按优先级排序 (大小写不敏感匹配).
# 列根目录文件后, 按此顺序查找第一个存在的中文 README.
CHINESE_README_FILENAMES = [
    "README.zh-CN.md",
    "README.zh.md",
    "README.zh-Hans.md",
    "README.zh-cn.md",
    "README-ZH.md",
    "README-zh-CN.md",
    "README-zh.md",
    "README_ZH.md",
    "README_CN.md",
    "README-zh_CN.md",
    "README.zh_CN.md",
    "README.CN.md",
    "README-CN.md",
    "README.cn.md",
    "README.chinese.md",
    "README.Chinese.md",
]


async def _fetch_readme_from_github(owner: str, repo_name: str) -> Optional[str]:
    """调用 GitHub API 获取 README 原文 (markdown/text). 优先中文 README, 无则回退默认.

    策略:
    1. GET /repos/{owner}/{repo}/contents 列出根目录文件
    2. 按 CHINESE_README_FILENAMES 优先级匹配第一个存在的中文 README
    3. 命中则通过 download_url 下载 raw 内容
    4. 未命中或列目录失败则回退 GET /repos/{owner}/{repo}/readme (GitHub 默认 README)
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "find-github-crawler/0.1",
    }
    token = (settings.GITHUB_TOKEN or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    list_url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo_name)}/contents"
    readme_url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo_name)}/readme"

    try:
        async with httpx.AsyncClient() as client:
            # 1. 列出根目录文件, 查找中文 README
            chinese_path: Optional[str] = None
            try:
                list_resp = await client.get(list_url, headers=headers, timeout=20.0)
                if list_resp.status_code == 200:
                    files = list_resp.json()
                    if isinstance(files, list):
                        # 构建文件名小写 -> 原始 path 的映射, 用于大小写不敏感匹配
                        name_index: Dict[str, str] = {}
                        for f in files:
                            if isinstance(f, dict):
                                name = f.get("name", "")
                                path = f.get("path", name)
                                if name:
                                    name_index[name.lower()] = path
                        for candidate in CHINESE_README_FILENAMES:
                            matched = name_index.get(candidate.lower())
                            if matched:
                                chinese_path = matched
                                break
            except (httpx.RequestError, httpx.HTTPError, ValueError) as e:
                logger.debug("List contents failed for %s/%s: %s", owner, repo_name, e)

            # 2. 命中中文 README: 通过 download_url 下载 raw 内容
            if chinese_path:
                # 重新 GET contents/{path} 拿 download_url (列表项里也有, 但保险起见单独取一次)
                file_url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo_name)}/contents/{quote(chinese_path, safe='/')}"
                try:
                    file_resp = await client.get(file_url, headers=headers, timeout=20.0)
                    if file_resp.status_code == 200:
                        file_data = file_resp.json()
                        download_url = file_data.get("download_url")
                        if download_url:
                            raw_resp = await client.get(download_url, headers=headers, timeout=30.0)
                            if raw_resp.status_code == 200:
                                logger.info(
                                    "Using Chinese README %s for %s/%s",
                                    chinese_path, owner, repo_name,
                                )
                                return raw_resp.text
                        # download_url 不可用时, 回退到 base64 content
                        content_b64 = file_data.get("content", "") or ""
                        if content_b64:
                            logger.info(
                                "Using Chinese README (base64) %s for %s/%s",
                                chinese_path, owner, repo_name,
                            )
                            return base64.b64decode(content_b64).decode("utf-8", errors="replace")
                except (httpx.RequestError, httpx.HTTPError) as e:
                    logger.warning(
                        "Failed to fetch Chinese README %s for %s/%s: %s",
                        chinese_path, owner, repo_name, e,
                    )

            # 3. 回退: 获取 GitHub 默认 README
            resp = await client.get(readme_url, headers=headers, timeout=20.0)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning(
                "GitHub README API returned status=%s for %s/%s",
                resp.status_code, owner, repo_name,
            )
            return None
        data = resp.json()
        content_b64 = data.get("content", "") or ""
        encoding = data.get("encoding", "base64")
        if encoding == "base64":
            return base64.b64decode(content_b64).decode("utf-8", errors="replace")
        return content_b64
    except (httpx.RequestError, httpx.HTTPError) as e:
        logger.warning("Failed to fetch README for %s/%s: %s", owner, repo_name, e)
        return None


@router.get("/{repo_id}/readme")
async def get_repo_readme(repo_id: int, db: Session = Depends(get_db)) -> dict:
    """获取仓库 README (5 分钟内存缓存, 命中缓存直接返回, 否则从 GitHub 拉取)."""
    cached = _README_CACHE.get(repo_id)
    if cached is not None:
        return {"content": cached, "cached": True}

    repo = db.query(Repository).get(repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    content = await _fetch_readme_from_github(repo.owner, repo.name)
    if content is None:
        raise HTTPException(status_code=404, detail="README not found or repo has no README")

    _README_CACHE.set(repo_id, content)
    return {"content": content, "cached": False}


async def _translate_readme_via_llm(content: str) -> str:
    """调用 LLM 将 README 翻译为中文, 保持 Markdown 格式完全不变.

    要求 LLM 只返回翻译后的文档内容, 不要任何额外解释.
    """
    # R3: prompt 注入防护 - 截断超长内容, 避免恶意 README 污染指令或消耗过多 token.
    MAX_TRANSLATE_CHARS = 50_000
    if len(content) > MAX_TRANSLATE_CHARS:
        content = content[:MAX_TRANSLATE_CHARS] + "\n\n<!-- 内容过长, 已截断 -->"
    if not settings.llm_enabled:
        raise HTTPException(status_code=503, detail="LLM 未配置, 无法翻译. 请设置 LLM_API_KEY.")
    if not content.strip():
        raise HTTPException(status_code=400, detail="README 内容为空, 无需翻译.")

    prompt = (
        "将以下 Markdown 文档翻译为简体中文。\n"
        "严格要求:\n"
        "1. 保持所有 Markdown 格式完全不变 (标题、代码块、链接、图片、表格、列表、引用、HTML 标签等)\n"
        "2. 代码块内的代码不翻译, 只翻译代码块的注释和说明文字\n"
        "3. 保持所有 URL、图片路径、变量名、命令行指令不变\n"
        "4. 只返回翻译后的文档内容, 不要添加任何解释、前言、后记或 markdown 代码块包裹\n"
        "5. 如果文档已经是中文, 直接返回原文\n\n"
        f"文档内容:\n{content}"
    )

    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你是一个专业的技术文档翻译器。你只输出翻译结果, 绝不输出任何额外文字。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 8000,
    }
    headers = {"Content-Type": "application/json"}
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.LLM_API_BASE}/chat/completions",
                json=payload,
                headers=headers,
                timeout=max(settings.LLM_TIMEOUT, 120),
            )
        if resp.status_code != 200:
            # R6: 不把上游响应体返给前端, 仅记日志
            logger.warning(
                "LLM translate failed: status=%s repo body=%s",
                resp.status_code, resp.text[:300],
            )
            raise HTTPException(
                status_code=502,
                detail=f"LLM 翻译失败 (上游状态 {resp.status_code})",
            )
        data = resp.json()
        translated = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        )
        if not translated:
            raise HTTPException(status_code=502, detail="LLM 返回空内容.")
        # 去除 LLM 可能添加的 markdown 代码块包裹
        if translated.startswith("```"):
            lines = translated.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            translated = "\n".join(lines).strip()
        return translated
    except httpx.RequestError as e:
        # R6: 不把异常细节(可能含 URL/key)返给前端
        logger.warning("LLM translate request error: %s", e)
        raise HTTPException(status_code=502, detail="LLM 请求失败, 请稍后重试")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("LLM translate unexpected error")
        raise HTTPException(status_code=500, detail="翻译过程中发生内部错误")


@router.post("/{repo_id}/translate-readme")
async def translate_repo_readme(repo_id: int, db: Session = Depends(get_db)) -> dict:
    """AI 翻译 README 为中文 (保持 Markdown 格式). 翻译结果内存缓存, 下次秒回."""
    # 命中翻译缓存
    cached_translated = _TRANSLATE_CACHE.get(repo_id)
    if cached_translated is not None:
        return {"content": cached_translated, "cached": True}

    # 获取 README 原文 (优先内存缓存, 否则从 GitHub 拉取)
    cached = _README_CACHE.get(repo_id)
    if cached is not None:
        content = cached
    else:
        repo = db.query(Repository).get(repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        content = await _fetch_readme_from_github(repo.owner, repo.name)
        if content is None:
            raise HTTPException(
                status_code=404, detail="README not found or repo has no README"
            )
        _README_CACHE.set(repo_id, content)

    translated = await _translate_readme_via_llm(content)
    _TRANSLATE_CACHE.set(repo_id, translated)
    return {"content": translated, "cached": False}
