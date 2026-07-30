"""ORM 模型: Repository / Snapshot / CrawlRun / AIInterpretation."""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .tz import now_cn


class Repository(Base):
    """GitHub 仓库主表. 按 github_id 唯一, 多次抓取会更新计数."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    github_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_type: Mapped[Optional[str]] = mapped_column(String(50))
    html_url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    language: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    topics: Mapped[Optional[List[str]]] = mapped_column(JSON, default=list)
    license: Mapped[Optional[str]] = mapped_column(String(100), index=True)

    stargazers_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    forks_count: Mapped[int] = mapped_column(Integer, default=0)
    watchers_count: Mapped[int] = mapped_column(Integer, default=0)
    open_issues_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    pushed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn, index=True)

    # ===== 产品扩展字段 =====
    # owner 地区 (china / overseas / unknown)
    region: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    is_chinese_owner: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 是否有中文文档 (README.zh.md 或 README 含中文)
    has_chinese_doc: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 技术领域分类 (frontend/backend/ai/devops/security/database/tools/efficiency/...)
    category: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    # 是否为效率工具
    is_efficiency_tool: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 效率工具的行业分类 (developer/design/pm/writing/data/office/ai-assistant/...)
    industry: Mapped[Optional[str]] = mapped_column(String(50), index=True)

    snapshots: Mapped[List["Snapshot"]] = relationship(
        "Snapshot", back_populates="repository", cascade="all, delete-orphan"
    )
    interpretations: Mapped[List["AIInterpretation"]] = relationship(
        "AIInterpretation", back_populates="repository", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Repository {self.full_name} ★{self.stargazers_count}>"


class Snapshot(Base):
    """每次抓取的快照, 用于绘制 star 趋势."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    stars_at_snapshot: Mapped[int] = mapped_column(Integer, default=0)
    forks_at_snapshot: Mapped[int] = mapped_column(Integer, default=0)
    stars_gained: Mapped[int] = mapped_column(Integer, default=0)
    # 热度评分 = ΔStars × (1/log10(TotalStars+10)) × e^(-λ×AgeInDays)
    # 用于防止老牌大项目霸榜, 提升低基数爆款黑马权重
    score: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    rank_in_period: Mapped[Optional[int]] = mapped_column(Integer)
    crawl_run_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("crawl_runs.id", ondelete="SET NULL"), index=True
    )

    repository: Mapped["Repository"] = relationship("Repository", back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint(
            "repository_id", "period", "snapshot_date", name="uq_repo_period_date"
        ),
    )

    def __repr__(self) -> str:
        return f"<Snapshot repo={self.repository_id} {self.period}@{self.snapshot_date} ★{self.stars_at_snapshot}>"


class CrawlRun(Base):
    """抓取运行记录."""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    period: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    total_repos_found: Mapped[int] = mapped_column(Integer, default=0)
    total_repos_upserted: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    params: Mapped[Optional[dict]] = mapped_column(JSON)

    def __repr__(self) -> str:
        return f"<CrawlRun #{self.id} {self.period} {self.status}>"


class AIInterpretation(Base):
    """AI 中文解读 - 每个仓库的中文简介、难度评分等."""

    __tablename__ = "ai_interpretations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 中文一句话简介
    summary_cn: Mapped[Optional[str]] = mapped_column(Text)
    # 价值解读: 解决什么问题、适合谁
    value_prop: Mapped[Optional[str]] = mapped_column(Text)
    # 上手难度 1-5
    difficulty: Mapped[Optional[int]] = mapped_column(Integer)
    # 预计学习时长 (小时)
    learning_hours: Mapped[Optional[int]] = mapped_column(Integer)
    # 适合人群
    suitable_for: Mapped[Optional[str]] = mapped_column(Text)
    # 替代品/竞品
    alternatives: Mapped[Optional[str]] = mapped_column(Text)
    # LLM 模型名
    model: Mapped[Optional[str]] = mapped_column(String(100))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn, index=True)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="interpretations")

    def __repr__(self) -> str:
        return f"<AIInterpretation repo={self.repository_id} diff={self.difficulty}>"


class TrendingCache(Base):
    """趋势榜单缓存表 - 抓取完成后预计算, 供 /api/v1/trending 高性能读取.

    每次 crawl 完成后, 按 (time_window, language) 预计算 Top N 写入此表.
    API 优先读缓存, 避免实时 JOIN + 排序.
    """

    __tablename__ = "trending_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time_window: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # daily/weekly/monthly
    language: Mapped[str] = mapped_column(String(50), default="all", nullable=False, index=True)  # all/python/...
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    delta_stars: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn, index=True)

    repository: Mapped["Repository"] = relationship("Repository")

    __table_args__ = (
        UniqueConstraint("time_window", "language", "rank", name="uq_window_lang_rank"),
    )

    def __repr__(self) -> str:
        return f"<TrendingCache {self.time_window}/{self.language} #{self.rank} repo={self.repository_id}>"


class SnapshotMonthlySummary(Base):
    """快照月度摘要 - 归档任务将超过保留期的明细快照聚合后写入此表.

    长期运行后 snapshots 表行数膨胀 (每天 ~1500 条), 归档任务将 N 天前的明细按
    (repository_id, period, year, month) 聚合成月度摘要, 保留月末总 star、月内最大
    stars_gained / score 与快照条数, 然后删除已归档的明细, 控制表大小.
    """

    __tablename__ = "snapshot_monthly_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    # 月末 (该月最后一次快照) 的总 star 数
    month_end_stars: Mapped[int] = mapped_column(Integer, default=0)
    # 该月内 stars_gained 的最大值
    max_stars_gained: Mapped[int] = mapped_column(Integer, default=0)
    # 该月内 score 的最大值
    max_score: Mapped[float] = mapped_column(Float, default=0.0)
    # 该月聚合的明细快照条数
    snapshot_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint(
            "repository_id", "period", "year", "month", name="uq_repo_period_year_month"
        ),
    )

    def __repr__(self) -> str:
        return f"<SnapshotMonthlySummary repo={self.repository_id} {self.period} {self.year}-{self.month}>"
