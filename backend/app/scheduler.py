"""APScheduler 调度器 - 在 FastAPI startup 事件中启动."""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .crawler.tasks import archive_old_snapshots, crawl_period

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def _run_archive() -> None:
    """APScheduler 任务回调 - 归档超期快照."""
    try:
        result = archive_old_snapshots()
        logger.info(
            "Scheduled archive done: archived_rows=%d summary_upserted=%d cutoff=%s",
            result["archived_rows"], result["summary_upserted"], result["cutoff_date"],
        )
    except Exception:
        logger.exception("Scheduled archive failed")


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


def _sync_unclassified_after_crawl() -> None:
    """抓取成功后, 对未分类仓库用 LLM 兜底分类.

    仅补 category IS NULL 的仓库 (force=False), 避免对已分类仓库重复调用 LLM.
    分类结果供 trending 去同质化 (优先级6) 使用. 本次抓取的新仓库若被规则分类器
    漏掉, 此处补上 category, 下次构建 trending cache 时去同质化即可生效.
    """
    from .crawler.llm_classifier import classify_repos_with_llm

    if not settings.llm_enabled:
        logger.info("Post-crawl LLM classification skipped: LLM not enabled")
        return

    try:
        result = classify_repos_with_llm(repo_ids=None, limit=200, force=False)
        logger.info(
            "Post-crawl LLM classification done: total=%d success=%d failed=%d",
            result["total"], result["success"], result["failed"],
        )
    except Exception:
        logger.exception("Post-crawl LLM classification failed")


def _run_crawl(period: str, write_snapshot: bool = True) -> None:
    """APScheduler 任务回调 - 同步执行抓取, 成功后立即同步未解读仓库的 AI 解读.

    Args:
        period: daily / weekly / monthly
        write_snapshot: False 时仅刷新 Repository 表不写快照 (daily 12:00 刷新模式)
    """
    try:
        summary = crawl_period(period=period, write_snapshot=write_snapshot)
    except Exception:
        logger.exception("Scheduled crawl failed: period=%s", period)
        return

    if summary.status != "success":
        logger.warning(
            "Scheduled crawl did not succeed (status=%s), skipping interpretation", summary.status,
        )
        return

    # 仅完整抓取 (写快照) 后才触发 AI 解读; 刷新模式不引入新解读任务
    if not write_snapshot:
        return

    # 抓取成功后, 先用 LLM 兜底分类未分类仓库 (快, 限 200 个), 再全量 AI 解读.
    # 分类结果供 trending 去同质化 (优先级6) 使用; 本次新仓库下次构建缓存时生效.
    _sync_unclassified_after_crawl()
    # 抓取成功后, 立即调用全量 AI 解读 (仅未解读的仓库)
    _sync_uninterpreted_after_crawl()


def start_scheduler() -> BackgroundScheduler:
    """启动调度器并注册 cron 任务. 重复调用会先关闭旧实例."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    # 每日抓取: 00:00 完整抓取 (写快照, 建立差值基准)
    scheduler.add_job(
        _run_crawl,
        trigger=CronTrigger(hour=0, minute=0),
        id="crawl_daily",
        args=["daily"],
        kwargs={"write_snapshot": True},
        replace_existing=True,
    )
    # 每日刷新: 12:00 仅刷新 Repository 表 (不写快照, 避免覆盖 00:00 基准)
    scheduler.add_job(
        _run_crawl,
        trigger=CronTrigger(hour=12, minute=0),
        id="crawl_daily_refresh",
        args=["daily"],
        kwargs={"write_snapshot": False},
        replace_existing=True,
    )
    # 每周抓取: 每天 02:00 执行 (每天写快照, stars_gained = 今天 - 7天前快照)
    scheduler.add_job(
        _run_crawl,
        trigger=CronTrigger(hour=2, minute=0),
        id="crawl_weekly",
        args=["weekly"],
        replace_existing=True,
    )
    # 每月抓取: 每天 04:00 执行 (每天写快照, stars_gained = 今天 - 30天前快照)
    scheduler.add_job(
        _run_crawl,
        trigger=CronTrigger(hour=4, minute=0),
        id="crawl_monthly",
        args=["monthly"],
        replace_existing=True,
    )
    # 快照归档: 每天 03:00 将超期明细聚合成月度摘要后删除 (控制 snapshots 表大小)
    scheduler.add_job(
        _run_archive,
        trigger=CronTrigger(hour=3, minute=0),
        id="archive_snapshots",
        replace_existing=True,
    )

    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "APScheduler started. Jobs: crawl_daily(00:00 full), crawl_daily_refresh(12:00 refresh-only), "
        "crawl_weekly(02:00 daily), crawl_monthly(04:00 daily), archive_snapshots(03:00 daily)"
    )
    return scheduler


def stop_scheduler() -> None:
    """停止调度器."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("APScheduler stopped.")
