"""应用配置 - 通过环境变量读取."""

from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 项目根目录 (backend/)
BACKEND_DIR = Path(__file__).resolve().parent.parent
# 仓库根目录
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """应用配置. 从 .env 文件或环境变量读取."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # GitHub API
    GITHUB_TOKEN: str = ""
    GITHUB_CRAWL_THRESHOLD: int = 10
    GITHUB_CRAWL_TOP_LANGUAGES: int = 20
    GITHUB_CRAWL_MAX_PAGES_PER_QUERY: int = 10
    GITHUB_CRAWL_MAX_CANDIDATES: int = 500

    # LLM (兼容 OpenAI 格式: DeepSeek / 通义 / OpenAI / 本地 vllm 等)
    LLM_API_BASE: str = "https://api.deepseek.com/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"
    LLM_TIMEOUT: int = 60
    # AI 解读并发数
    LLM_CONCURRENCY: int = 5
    # 是否启用 AI 解读 (无 key 时设 false 跳过)
    AI_INTERPRETATION_ENABLED: bool = True

    # 数据库
    DATABASE_URL: str = "sqlite:///./data/findgithub.db"

    # 调度
    SCHEDULER_ENABLED: bool = True

    # CORS
    CORS_ORIGINS: str = "http://localhost:5173"

    # 管理员凭证 (用于敏感操作鉴权: 触发抓取/停止任务/全量AI解读)
    # 推荐: ADMIN_PASSWORD_HASH (PBKDF2 哈希, 运行 `python -m cli hashpw` 生成)
    # 兼容: ADMIN_PASSWORD (明文, 启动时 warning)
    # 两者均未配置时, 敏感接口返回 503 拒绝访问
    ADMIN_PASSWORD_HASH: str = ""
    ADMIN_PASSWORD: str = ""

    # 签名 token 密钥 (HMAC). 留空时启动自动生成随机值 (重启后已签发 token 失效).
    # 生产环境建议固定配置, 避免重启导致所有管理员掉线.
    SESSION_SECRET: str = ""

    # 管理员 token 有效期 (小时)
    SESSION_TTL_HOURS: int = 12

    @property
    def cors_origins_list(self) -> List[str]:
        """CORS 允许来源列表."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @field_validator("GITHUB_TOKEN")
    @classmethod
    def _strip_token(cls, v: str) -> str:
        return (v or "").strip()

    @property
    def llm_enabled(self) -> bool:
        """LLM 是否真正可用 (启用 且 配置了 key)."""
        return self.AI_INTERPRETATION_ENABLED and bool(self.LLM_API_KEY.strip())


settings = Settings()

# SESSION_SECRET 留空时自动生成随机值 (重启后失效, 仅开发环境适用)
if not settings.SESSION_SECRET.strip():
    import secrets as _secrets

    settings.SESSION_SECRET = _secrets.token_urlsafe(32)
    import logging as _logging

    _logging.getLogger(__name__).warning(
        "SESSION_SECRET 未配置, 已生成临时随机值. 重启后已签发的管理员 token 将失效. "
        "生产环境请在 .env 中固定配置 SESSION_SECRET."
    )
