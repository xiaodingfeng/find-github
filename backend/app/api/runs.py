"""/api/runs 端点 - 抓取运行记录 + 手动触发 + 手动停止."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import CrawlRun
from ..schemas import CrawlRunListOut, CrawlRunOut, TriggerCrawlIn, TriggerCrawlOut
from ..crawler.tasks import crawl_period, is_period_running, request_crawl_cancel
from ..security import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=CrawlRunListOut)
def list_runs(
    period: Optional[str] = Query(None, pattern="^(daily|weekly|monthly)$"),
    status: Optional[str] = Query(None, pattern="^(running|success|failed)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> CrawlRunListOut:
    """抓取运行记录列表."""
    query = db.query(CrawlRun)
    if period:
        query = query.filter(CrawlRun.period == period)
    if status:
        query = query.filter(CrawlRun.status == status)

    total = query.count()
    items = (
        query.order_by(CrawlRun.started_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return CrawlRunListOut(items=[CrawlRunOut.model_validate(it) for it in items], total=total)


@router.get("/{run_id}", response_model=CrawlRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)) -> CrawlRunOut:
    """获取单条抓取记录."""
    run = db.query(CrawlRun).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return CrawlRunOut.model_validate(run)


@router.post("/{run_id}/cancel")
def cancel_run(run_id: int, db: Session = Depends(get_db), _admin: None = Depends(require_admin)) -> dict:
    """手动停止正在运行的抓取任务.

    通过设置 threading.Event 信号, 让抓取线程在下一个循环检查点退出.
    返回立即, 实际停止会有 1-2 秒延迟 (等待当前 item 处理完成).

    对找不到 cancel 句柄的 running 任务 (如后端重启后遗留的僵尸记录),
    直接在 DB 中标记为 cancelled, 避免状态永久卡死.
    """
    from ..tz import now_cn

    run = db.query(CrawlRun).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.status != "running":
        return {
            "run_id": run_id,
            "status": run.status,
            "message": f"任务已结束 (状态: {run.status}), 无需停止",
        }

    ok = request_crawl_cancel(run_id)
    if ok:
        return {
            "run_id": run_id,
            "status": "cancelling",
            "message": "已发送停止信号, 任务将在 1-2 秒内停止",
        }

    # 找不到 cancel 句柄: 说明该 run 是后端重启前遗留的僵尸记录
    # 直接在 DB 中标记为 cancelled, 让前端可以清理
    run.status = "cancelled"
    run.finished_at = now_cn()
    run.error_message = "后端重启后检测为僵尸任务, 已自动标记为已停止"
    db.commit()
    return {
        "run_id": run_id,
        "status": "cancelled",
        "message": "该任务为遗留僵尸记录, 已直接标记为已停止",
    }


def _run_crawl_in_thread(period: str, threshold: Optional[int]) -> None:
    """后台线程执行抓取 (crawl_period 是同步阻塞调用)."""
    try:
        crawl_period(period=period, threshold=threshold)
    except Exception:
        logger.exception("Background crawl failed: period=%s", period)


@router.post("/trigger", response_model=TriggerCrawlOut)
def trigger_crawl(payload: TriggerCrawlIn, _admin: None = Depends(require_admin)) -> TriggerCrawlOut:
    """手动触发一次抓取 (异步执行). 返回最新 running 状态的 run_id.

    同一 period 已有抓取在运行时拒绝重复触发 (R5: 并发互斥).
    """
    if is_period_running(payload.period):
        raise HTTPException(
            status_code=409,
            detail=f"period={payload.period} 已有抓取任务在运行, 请等待完成或先停止.",
        )
    thread = threading.Thread(
        target=_run_crawl_in_thread,
        args=(payload.period, payload.threshold),
        daemon=True,
    )
    thread.start()

    # 等待 crawl_period 创建好 running 记录
    for _ in range(20):  # 最多等 2 秒
        time.sleep(0.1)
        db = SessionLocal()
        try:
            latest = (
                db.query(CrawlRun)
                .filter(CrawlRun.period == payload.period, CrawlRun.status == "running")
                .order_by(CrawlRun.started_at.desc())
                .first()
            )
            if latest:
                return TriggerCrawlOut(
                    run_id=latest.id,
                    status="running",
                    message=f"抓取已启动 (period={payload.period})",
                )
        finally:
            db.close()

    return TriggerCrawlOut(
        run_id=0,
        status="started",
        message="抓取任务已派发, 请稍后通过 GET /api/runs 查看结果",
    )
