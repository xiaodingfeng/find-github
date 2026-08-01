"""/api/interpret 端点 - AI 中文解读触发."""

from __future__ import annotations

import logging
import threading
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends

from ..config import settings
from ..crawler.interpreter import interpret_repos
from ..crawler.interpret_progress import get_interpret_progress
from ..schemas import InterpretRequestIn, InterpretResultOut
from ..security import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/interpret", tags=["interpret"])


def _run_interpret_in_thread(
    repo_ids: Optional[list],
    limit: int,
    force: bool,
    track_progress: bool = False,
) -> None:
    """后台线程执行 AI 解读 (避免阻塞 FastAPI 事件循环)."""
    try:
        interpret_repos(
            repo_ids=repo_ids, limit=limit, force=force, track_progress=track_progress,
        )
    except Exception:
        logger.exception("Background interpretation failed")


@router.post("", response_model=InterpretResultOut)
def trigger_interpret(
    payload: InterpretRequestIn,
    background_tasks: BackgroundTasks,
    _admin: None = Depends(require_admin),
) -> InterpretResultOut:
    """触发 AI 中文解读 (后台线程异步执行).

    - 不传 repo_ids: 自动选择未解读的仓库 (按 star 降序)
    - force=true: 强制重新生成
    """
    if not settings.llm_enabled:
        return InterpretResultOut(
            total=0,
            success=0,
            failed=0,
        )

    # 用独立线程执行, 避免 BackgroundTasks 阻塞事件循环
    thread = threading.Thread(
        target=_run_interpret_in_thread,
        args=(payload.repo_ids, payload.limit, payload.force),
        daemon=True,
    )
    thread.start()

    return InterpretResultOut(
        total=0,
        success=0,
        failed=0,
    )


@router.post("/sync", response_model=InterpretResultOut)
def trigger_interpret_sync(payload: InterpretRequestIn, _admin: None = Depends(require_admin)) -> InterpretResultOut:
    """同步执行 AI 解读 (阻塞直到完成). 适合小批量."""
    if not settings.llm_enabled:
        return InterpretResultOut(total=0, success=0, failed=0)
    result = interpret_repos(
        repo_ids=payload.repo_ids,
        limit=payload.limit,
        force=payload.force,
    )
    return InterpretResultOut(**result)


@router.post("/all", response_model=InterpretResultOut)
def trigger_interpret_all(_admin: None = Depends(require_admin)) -> InterpretResultOut:
    """全量 AI 解读 - 对所有**未解读**的仓库生成解读 (后台线程).

    生产环境大部分仓库已解读过, 这里 force=False 仅筛选没有 AI 解读的仓库,
    避免对已解读的仓库重复调用 LLM. 单个仓库需要重新解读时, 由仓库列表/详情页
    的"重新解读"入口 (走 /api/interpret 或 /api/interpret/sync, force=true) 处理,
    解读结果保存后会自动同步到所有页面 (latest_interpretation 查询实时生效).

    并行度由 LLM_CONCURRENCY 配置项控制.
    进度可通过 GET /api/interpret/progress 查询.
    """
    if not settings.llm_enabled:
        return InterpretResultOut(total=0, success=0, failed=0)

    # 若已有全量解读在运行, 拒绝重复触发
    progress = get_interpret_progress()
    if progress.get("running"):
        return InterpretResultOut(total=0, success=0, failed=0)

    # force=False: 仅未解读的仓库; track_progress=True 写入全局进度面板
    thread = threading.Thread(
        target=_run_interpret_in_thread,
        args=(None, 999999, False, True),
        daemon=True,
    )
    thread.start()

    logger.info("Full interpretation triggered (uninterpreted repos only, force=false)")
    return InterpretResultOut(
        total=0,
        success=0,
        failed=0,
    )


@router.post("/classify", response_model=InterpretResultOut)
def trigger_classify(payload: InterpretRequestIn, _admin: None = Depends(require_admin)) -> InterpretResultOut:
    """用 LLM 对未分类仓库兜底分类 (同步执行, 适合小批量).

    - 不传 repo_ids: 自动选 category IS NULL 的仓库 (按 star 降序)
    - force=true: 强制重新分类 (忽略已有 category)
    """
    if not settings.llm_enabled:
        return InterpretResultOut(total=0, success=0, failed=0)

    from ..crawler.llm_classifier import classify_repos_with_llm

    result = classify_repos_with_llm(
        repo_ids=payload.repo_ids,
        limit=payload.limit,
        force=payload.force,
    )
    return InterpretResultOut(**result)


@router.get("/progress")
def get_progress() -> dict:
    """查询全量 AI 解读进度."""
    return get_interpret_progress()
