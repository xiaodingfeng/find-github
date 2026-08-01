"""数据库引擎与会话管理."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import PROJECT_ROOT, settings


def _resolve_database_url(url: str) -> str:
    """将相对路径 sqlite URL 解析为绝对路径, 确保文件落在项目根目录下.

    支持的形式:
      sqlite:///./data/findgithub.db  -> sqlite:///<project_root>/data/findgithub.db
      sqlite:///data/findgithub.db    -> sqlite:///<project_root>/data/findgithub.db
    """
    if url.startswith("sqlite:///./"):
        rel = url.replace("sqlite:///./", "", 1)  # e.g. "data/findgithub.db"
    elif url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        rel = url.replace("sqlite:///", "", 1)  # e.g. "data/findgithub.db"
        # 如果已经是绝对路径 (Windows 盘符 / Unix /), 不再拼接
        if rel.startswith(("/", "\\")) or (len(rel) > 1 and rel[1] == ":"):
            return url
    else:
        return url

    abs_path = (PROJECT_ROOT / rel).resolve()
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{abs_path.as_posix()}"


DATABASE_URL = _resolve_database_url(settings.DATABASE_URL)
_IS_SQLITE = DATABASE_URL.startswith("sqlite")

# SQLite 需要 check_same_thread=False 以便 FastAPI 多线程访问
connect_args = {"check_same_thread": False} if _IS_SQLITE else {}

# pool_pre_ping 避免 MySQL/PG 长连接断开; SQLite 用 SingletonThreadPool 风格
# 但 FastAPI 多线程并发, SQLite 默认 QueuePool 即可 (配合 check_same_thread=False)
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=not _IS_SQLITE,
    pool_size=10 if not _IS_SQLITE else 5,
    max_overflow=20 if not _IS_SQLITE else 10,
    pool_recycle=1800,
)


# ===== SQLite 性能 PRAGMA =====
# 在每个新连接上设置 PRAGMA, 让 SQLite 走 WAL + 大缓存 + 异步落盘,
# 默认 synchronous=FULL 每次都 fsync, 在抓取并发场景下接口会被严重阻塞.
if _IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        # WAL 模式: 读写不互斥, 读操作不被写阻塞 (关键)
        cursor.execute("PRAGMA journal_mode=WAL")
        # NORMAL: 只在 checkpoint 时 fsync, 减少 90%+ 写盘开销
        cursor.execute("PRAGMA synchronous=NORMAL")
        # 缓存 64MB (默认仅 2MB), 让索引/热数据常驻内存
        cursor.execute("PRAGMA cache_size=-65536")
        # 临时表/排序用内存而非磁盘
        cursor.execute("PRAGMA temp_store=MEMORY")
        # WAL 文件大小上限 64MB, 避免无限增长
        cursor.execute("PRAGMA journal_size_limit=67108864")
        # 稍放宽冲突策略, BUSY 时最多等 5 秒
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类."""

    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入用的数据库会话生成器."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表. 导入模型后调用 Base.metadata.create_all, 并补充缺失列与索引."""
    from . import models  # noqa: F401  确保模型被注册

    Base.metadata.create_all(bind=engine)
    # 对已存在的表补加新列 (create_all 不会 ALTER 已有表, 需手动迁移)
    _ensure_columns()
    # 对已存在的表补建索引 (CREATE INDEX IF NOT EXISTS 是幂等的)
    _ensure_indexes()


def _ensure_columns() -> None:
    """对已存在的表补加新列 (create_all 不会 ALTER 已有表).

    项目无 Alembic, 新增字段通过此函数幂等迁移: 先 PRAGMA table_info 检查列是否存在,
    不存在则 ALTER TABLE ADD COLUMN. 仅支持 SQLite (生产用 SQLite).
    每次新增字段在此追加一个检查块, 保持向后兼容.
    """
    if not _IS_SQLITE:
        return
    # (表名, 列名, 列定义)
    migrations = [
        ("snapshots", "is_estimated", "BOOLEAN DEFAULT 0"),
        ("trending_cache", "is_estimated", "BOOLEAN DEFAULT 0"),
    ]
    with engine.connect() as conn:
        for table, column, coldef in migrations:
            cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))]
            if column not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}"))
                conn.commit()


def _ensure_indexes() -> None:
    """对已有数据库补建关键复合索引. 仅 SQLite/PostgreSQL 通用语法.

    这些索引覆盖了最高频的查询路径:
    - (period, repository_id, snapshot_date): stats / repos / trending 的最新快照聚合
    - (repository_id, period): 按 repo + period 查快照
    - (repository_id, generated_at): 最新 AI 解读
    - (time_window, language, rank): trending cache 已通过 UniqueConstraint 覆盖
    """
    if not _IS_SQLITE:
        return
    statements = [
        # Snapshot 高频查询路径: WHERE period=? GROUP BY repository_id; WHERE repository_id=? AND period=?
        "CREATE INDEX IF NOT EXISTS ix_snapshots_period_repo_date ON snapshots (period, repository_id, snapshot_date)",
        "CREATE INDEX IF NOT EXISTS ix_snapshots_repo_period ON snapshots (repository_id, period)",
        # 仪表盘时间线: WHERE snapshot_date>=? GROUP BY snapshot_date, period
        # 数据增长后 snapshots 表是最大的, 按日期前缀过滤避免全表扫描
        "CREATE INDEX IF NOT EXISTS ix_snapshots_date_period ON snapshots (snapshot_date, period)",
        # AIInterpretation 最新解读查询: WHERE repository_id IN (...) GROUP BY repository_id; ORDER BY generated_at DESC
        "CREATE INDEX IF NOT EXISTS ix_interp_repo_generated ON ai_interpretations (repository_id, generated_at DESC)",
        # Repository 常用筛选组合
        "CREATE INDEX IF NOT EXISTS ix_repos_chinese_stars ON repositories (is_chinese_owner, stargazers_count DESC)",
        "CREATE INDEX IF NOT EXISTS ix_repos_eff_industry ON repositories (is_efficiency_tool, industry)",
        "CREATE INDEX IF NOT EXISTS ix_repos_category_stars ON repositories (category, stargazers_count DESC)",
        # 抓取记录列表: WHERE period=? ORDER BY started_at DESC (runs 表按 period 筛选 + 时间倒序)
        "CREATE INDEX IF NOT EXISTS ix_runs_period_started ON crawl_runs (period, started_at DESC)",
    ]
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))
