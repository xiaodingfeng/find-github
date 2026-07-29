"""APScheduler 调度器 - 在 FastAPI startup 事件中启动."""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .crawler.tasks import crawl_period

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def _sync_uninterpreted_after_crawl() -> None:
    """抓取成功后, 对没有 AI 解读的仓库进行全量解读.

    与 /api/interpret/all (页面"全量 AI 解读") 逻辑一致, 但仅同步未解读的仓库
    (force=False), 避免对已有解读的仓库重复调用 LLM. 进度通过全局 interpret_progress
    暴露, 前端 RunsPage 进度面板会实时展示.
    """
    from .crawler.interpret_progress import get_interpret_progress
    from .crawler.interpreter import interpret_repos

    # 若已有解读任务在运行 (例如用户手动触发了 /interpret/all), 跳过避免并发冲突.
    # 下一次定时抓取完成后会再次尝试, 未解读的仓库最终都会被覆盖.
    if get_interpret_progress().get("running"):
        logger.info("Post-crawl interpretation skipped: an interpretation is already running")
        return

    if not settings.llm_enabled:
        logger.info("Post-crawl interpretation skipped: LLM not enabled")
        return

    try:
        interpret_repos(repo_ids=None, limit=999999, force=False, track_progress=True)
    except Exception:
        logger.exception("Post-crawl interpretation sync failed")


def _run_crawl(period: str) -> None:
    """APScheduler 任务回调 - 同步执行抓取, 成功后立即同步未解读仓库的 AI 解读."""
    try:
        summary = crawl_period(period=period)
    except Exception:
        logger.exception("Scheduled crawl failed: period=%s", period)
        return

    if summary.status != "success":
        logger.warning(
            "Scheduled crawl did not succeed (status=%s), skipping interpretation", summary.status,
        )
        return

    # 抓取成功后, 立即调用全量 AI 解读 (仅未解读的仓库)
    _sync_uninterpreted_after_crawl()


def start_scheduler() -> BackgroundScheduler:
    """启动调度器并注册 cron 任务. 重复调用会先关闭旧实例."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    # 每日抓取: 00:00 和 12:00 (每 12 小时, 让 stars_gained 增量更精确)
    scheduler.add_job(
        _run_crawl,
        trigger=CronTrigger(hour="0,12", minute=0),
        id="crawl_daily",
        args=["daily"],
        replace_existing=True,
    )
    # 每周抓取: 每天 00:30 执行 (每天写快照, stars_gained = 今天 - 7天前快照)
    scheduler.add_job(
        _run_crawl,
        trigger=CronTrigger(hour=0, minute=30),
        id="crawl_weekly",
        args=["weekly"],
        replace_existing=True,
    )
    # 每月抓取: 每天 01:00 执行 (每天写快照, stars_gained = 今天 - 30天前快照)
    scheduler.add_job(
        _run_crawl,
        trigger=CronTrigger(hour=1, minute=0),
        id="crawl_monthly",
        args=["monthly"],
        replace_existing=True,
    )

    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "APScheduler started. Jobs: crawl_daily(00:00,12:00), crawl_weekly(00:30 daily), crawl_monthly(01:00 daily)"
    )
    return scheduler


def stop_scheduler() -> None:
    """停止调度器."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("APScheduler stopped.")
