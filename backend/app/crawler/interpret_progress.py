"""AI 解读进度跟踪 (进程内全局状态).

后台线程写入, API 读取, 单进程内共享.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InterpretProgress:
    """全量 AI 解读进度状态."""
    running: bool = False
    total: int = 0
    success: int = 0
    failed: int = 0
    current_repo: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: str = ""

    @property
    def processed(self) -> int:
        return self.success + self.failed

    @property
    def progress_percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return round(self.processed / self.total * 100, 1)

    @property
    def success_rate(self) -> float:
        if self.processed <= 0:
            return 0.0
        return round(self.success / self.processed * 100, 1)

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "total": self.total,
            "success": self.success,
            "failed": self.failed,
            "processed": self.processed,
            "progress_percent": self.progress_percent,
            "success_rate": self.success_rate,
            "current_repo": self.current_repo,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


# 全局单例 (进程内共享)
_progress = InterpretProgress()
_lock = threading.Lock()


def get_interpret_progress() -> dict:
    """获取当前全量解读进度."""
    with _lock:
        return _progress.to_dict()


def set_interpret_started(total: int, started_at: str) -> None:
    with _lock:
        _progress.running = True
        _progress.total = total
        _progress.success = 0
        _progress.failed = 0
        _progress.current_repo = ""
        _progress.started_at = started_at
        _progress.finished_at = ""
        _progress.error = ""


def set_interpret_progress(success: int, failed: int, current_repo: str = "") -> None:
    with _lock:
        _progress.success = success
        _progress.failed = failed
        if current_repo:
            _progress.current_repo = current_repo


def set_interpret_finished(success: int, failed: int, finished_at: str, error: str = "") -> None:
    with _lock:
        _progress.running = False
        _progress.success = success
        _progress.failed = failed
        _progress.finished_at = finished_at
        _progress.error = error
        _progress.current_repo = ""
