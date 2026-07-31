"""Pydantic 响应模型."""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class VerifyOut(BaseModel):
    """管理员验证响应."""

    ok: bool
    message: str = ""
    token: str = ""


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    snapshot_date: date
    period: str
    stars_at_snapshot: int
    forks_at_snapshot: int
    stars_gained: int = 0
    rank_in_period: Optional[int] = None
    crawl_run_id: Optional[int] = None


class AIInterpretationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    summary_cn: Optional[str] = None
    value_prop: Optional[str] = None
    difficulty: Optional[int] = None
    learning_hours: Optional[int] = None
    suitable_for: Optional[str] = None
    alternatives: Optional[str] = None
    model: Optional[str] = None
    generated_at: datetime


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    github_id: int
    name: str
    full_name: str
    owner: str
    owner_type: Optional[str] = None
    html_url: str
    description: Optional[str] = None
    language: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    license: Optional[str] = None
    stargazers_count: int
    forks_count: int
    watchers_count: int
    open_issues_count: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    pushed_at: Optional[datetime] = None
    first_seen_at: datetime
    last_seen_at: datetime
    # 产品扩展字段
    region: str = "unknown"
    is_chinese_owner: bool = False
    has_chinese_doc: bool = False
    category: Optional[str] = None
    is_efficiency_tool: bool = False
    industry: Optional[str] = None
    # 列表场景附加字段 (按 period 最新快照计算, period=all 时为 None)
    stars_gained: Optional[int] = None
    latest_interpretation: Optional[AIInterpretationOut] = None


class RepositoryDetailOut(RepositoryOut):
    snapshots: List[SnapshotOut] = Field(default_factory=list)
    interpretations: List[AIInterpretationOut] = Field(default_factory=list)


class RepositoryAggregates(BaseModel):
    """聚合统计: 当前筛选条件下的全量统计 (跨所有分页)."""
    total_stars: int
    total_forks: int
    total_gained: int
    chinese_count: int
    interpreted_count: int


class RepositoryListOut(BaseModel):
    items: List[RepositoryOut]
    total: int
    page: int
    per_page: int
    aggregates: Optional[RepositoryAggregates] = None


class CrawlRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    period: str
    status: str
    total_repos_found: int
    total_repos_upserted: int
    error_message: Optional[str] = None
    params: Optional[dict] = None


class CrawlRunListOut(BaseModel):
    items: List[CrawlRunOut]
    total: int


class TriggerCrawlIn(BaseModel):
    period: str = Field(..., pattern="^(daily|weekly|monthly)$")
    threshold: Optional[int] = None


class TriggerCrawlOut(BaseModel):
    run_id: int
    status: str
    message: str


class SummaryOut(BaseModel):
    total_repos: int
    total_runs: int
    by_period: dict
    recent_runs: List[CrawlRunOut]
    avg_stars: float
    top_language: Optional[str]
    # 扩展统计
    chinese_repos: int = 0
    efficiency_tools: int = 0
    interpreted_repos: int = 0


class LanguageStatOut(BaseModel):
    language: Optional[str]
    count: int


class TopRepoOut(BaseModel):
    repo: RepositoryOut
    metric_value: int


class TimelinePointOut(BaseModel):
    date: date
    period: str
    count: int


class CategoryStatOut(BaseModel):
    """技术领域分布统计."""
    category: Optional[str]
    count: int
    total_stars: int
    total_stars_gained: int


class RadarPointOut(BaseModel):
    """技术雷达数据点."""
    category: str
    repos: int
    stars_gained: int
    total_stars: int
    top_repos: List[TopRepoOut]


class IndustryStatOut(BaseModel):
    """效率工具行业分布."""
    industry: Optional[str]
    count: int
    total_stars: int


class TrendingItemOut(BaseModel):
    """Trending 榜单条目."""
    rank: int
    repo: RepositoryOut
    delta_stars: int
    score: float


class TrendingListOut(BaseModel):
    """Trending 榜单响应."""
    time_window: str
    language: str
    total: int
    page: int
    limit: int
    items: List[TrendingItemOut]
    cache_hit: bool = False


class InterpretRequestIn(BaseModel):
    """触发 AI 解读请求."""
    repo_ids: Optional[List[int]] = None
    limit: int = Field(50, ge=1, le=500)
    force: bool = False


class InterpretResultOut(BaseModel):
    total: int
    success: int
    failed: int
