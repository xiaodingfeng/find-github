"""FastAPI 应用入口."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .api import auth, interpret, repos, runs, stats, trending
from .scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 启动时建表 + 启动调度器, 关闭时停止调度器."""
    logger.info("Initializing database...")
    init_db()

    if settings.SCHEDULER_ENABLED:
        start_scheduler()
    else:
        logger.info("SCHEDULER_ENABLED=false, skipping APScheduler startup.")

    yield

    if settings.SCHEDULER_ENABLED:
        stop_scheduler()


app = FastAPI(
    title="GitHub 热门仓库发现与可视化系统",
    description="定时抓取 GitHub 热门仓库 (daily/weekly/monthly), 入库存储, 提供多维筛选的前端展示.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS (R4: 收紧 methods/headers, 仅允许实际需要的)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
)

# 注册 API 路由
app.include_router(auth.router)
app.include_router(repos.router)
app.include_router(stats.router)
app.include_router(runs.router)
app.include_router(interpret.router)
app.include_router(trending.router)


@app.get("/api/health")
def health() -> dict:
    """健康检查 (N3: 不泄露内部配置细节, 仅返回运行状态)."""
    return {
        "status": "ok",
        "scheduler_enabled": settings.SCHEDULER_ENABLED,
    }


# 前端静态文件托管 (生产环境). 仅在 frontend/dist 存在时挂载.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
_FRONTEND_DIST_RESOLVED = _FRONTEND_DIST.resolve() if _FRONTEND_DIST.exists() else None
if _FRONTEND_DIST_RESOLVED is not None:
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST_RESOLVED / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        """SPA fallback: 非文件请求返回 index.html.

        C3: 严格校验解析后的路径仍在 dist 目录内, 防止路径遍历 (../../etc/passwd).
        """
        # 拒绝包含路径遍历片段的请求
        if not full_path or ".." in full_path.split("/"):
            return FileResponse(_FRONTEND_DIST_RESOLVED / "index.html")
        candidate = (_FRONTEND_DIST_RESOLVED / full_path).resolve()
        # 必须仍在 dist 目录之下
        try:
            candidate.relative_to(_FRONTEND_DIST_RESOLVED)
        except ValueError:
            return FileResponse(_FRONTEND_DIST_RESOLVED / "index.html")
        if candidate.is_file():
            return FileResponse(candidate)
        # 否则返回 index.html (SPA 路由)
        return FileResponse(_FRONTEND_DIST_RESOLVED / "index.html")

    logger.info("Frontend static files mounted from %s", _FRONTEND_DIST_RESOLVED)
else:
    logger.info(
        "Frontend dist not found at %s. Run `npm run build` in frontend/ to enable SPA serving.",
        _FRONTEND_DIST,
    )
