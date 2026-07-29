"""时区工具 - 统一使用东八区 (Asia/Shanghai).

所有存储在 DB 中的 naive datetime 均为东八区时间.
GitHub API 返回的 UTC 时间通过 to_cn_datetime 转为东八区.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 东八区
CST = timezone(timedelta(hours=8))


def now_cn() -> datetime:
    """返回东八区当前时间 (naive, 去掉 tzinfo 以兼容 SQLite DateTime 列)."""
    return datetime.now(CST).replace(tzinfo=None)


def to_cn_datetime(value: datetime) -> datetime:
    """将任意带时区的 datetime 转为东八区 naive datetime.

    若 value 无时区信息, 假定其为 UTC.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CST).replace(tzinfo=None)
