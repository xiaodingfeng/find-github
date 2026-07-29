"""安全模块 - 密码哈希、签名 token、鉴权依赖、IP 限流.

仅依赖 Python 标准库 (hashlib/hmac/secrets/base64/json/time/collections),
避免引入 passlib/pyjwt/slowapi 等第三方包.

支持两种管理员凭证配置 (优先级从高到低):
1. ADMIN_PASSWORD_HASH: pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
2. ADMIN_PASSWORD: 明文密码 (向后兼容, 启动时 warning 并在内存中转为哈希)

未配置任何凭证时, 敏感接口直接拒绝 (不再"免验证放行").
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from collections import OrderedDict
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status

from .config import settings

logger = logging.getLogger(__name__)

# ===== 密码哈希 (PBKDF2-HMAC-SHA256) =====
_PBKDF2_ITERATIONS = 200_000
_HASH_ALGO = "pbkdf2_sha256"


def hash_password(plain: str) -> str:
    """对明文密码做 PBKDF2 哈希, 返回可存储的字符串."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "{}${}${}${}".format(
        _HASH_ALGO,
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(plain: str, stored: str) -> bool:
    """恒定时间校验密码. stored 可为哈希字符串或明文 (兼容旧配置)."""
    if not stored or not plain:
        return False
    # 兼容明文配置 (ADMIN_PASSWORD): 恒定时间比较
    if "$" not in stored:
        return hmac.compare_digest(plain, stored)
    # 哈希校验
    try:
        parts = stored.split("$")
        if len(parts) != 4 or parts[0] != _HASH_ALGO:
            return False
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2])
        expected = base64.b64decode(parts[3])
        dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def _resolve_admin_credentials() -> Optional[str]:
    """解析管理员凭证, 返回用于校验的存储值 (哈希或明文).

    未配置任何凭证时返回 None, 调用方应拒绝敏感操作.
    """
    # 优先使用哈希配置
    hash_cfg = (settings.ADMIN_PASSWORD_HASH or "").strip()
    if hash_cfg:
        return hash_cfg
    # 向后兼容: 明文配置
    plain = (settings.ADMIN_PASSWORD or "").strip()
    if plain:
        if not getattr(_resolve_admin_credentials, "_warned", False):
            logger.warning(
                "ADMIN_PASSWORD 配置为明文, 建议改用 ADMIN_PASSWORD_HASH (运行 "
                "`python -m cli hashpw` 生成). 明文配置将在内存中按恒定时间比较."
            )
            _resolve_admin_credentials._warned = True  # type: ignore[attr-defined]
        return plain
    return None


# ===== 签名 token (HMAC-SHA256, 无状态) =====
# token = b64url(payload_json) + "." + b64url(signature)
# payload = {"exp": unix_ts}


def _sign(payload: dict) -> str:
    secret = settings.SESSION_SECRET.encode("utf-8")
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret, body, hashlib.sha256).digest()
    return body.decode("ascii") + "." + base64.urlsafe_b64encode(sig).decode("ascii")


def issue_admin_token(password: str) -> Optional[str]:
    """校验密码并签发短期 token. 密码错误或未配置凭证时返回 None."""
    stored = _resolve_admin_credentials()
    if stored is None:
        # 未配置凭证: 拒绝 (不再放行)
        return None
    if not verify_password(password, stored):
        return None
    exp = int(time.time()) + settings.SESSION_TTL_HOURS * 3600
    return _sign({"exp": exp, "role": "admin"})


def verify_admin_token(token: str) -> bool:
    """校验签名 token, 过期或签名错误返回 False."""
    if not token or "." not in token:
        return False
    body, sig = token.rsplit(".", 1)
    try:
        body_bytes = base64.urlsafe_b64decode(body.encode("ascii"))
        sig_bytes = base64.urlsafe_b64decode(sig.encode("ascii"))
    except (ValueError, TypeError):
        return False
    # HMAC 输入必须与 _sign 一致: 对 body 的 base64 编码字节计算, 而非解码后的字节
    expected = hmac.new(
        settings.SESSION_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(sig_bytes, expected):
        return False
    try:
        payload = json.loads(body_bytes)
    except (ValueError, TypeError):
        return False
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time():
        return False
    return payload.get("role") == "admin"


# ===== FastAPI 鉴权依赖 =====

def require_admin(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
) -> None:
    """敏感接口鉴权依赖. 要求请求方携带有效签名 token.

    支持两种传递方式:
    - Authorization: Bearer <token>
    - X-Admin-Token: <token>
    """
    # 未配置凭证时直接拒绝 (N1: 不再"免验证放行")
    if _resolve_admin_credentials() is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="未配置管理员凭证 (ADMIN_PASSWORD_HASH), 敏感操作不可用.",
        )

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_admin_token:
        token = x_admin_token.strip()

    if not token or not verify_admin_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未授权或 token 已过期, 请重新验证.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ===== IP 限流 (滑动窗口, 进程内) =====

class IPRateLimiter:
    """简单的进程内 IP 滑动窗口限流器.

    每个 IP 维护一个时间戳队列, 超过窗口的记录被淘汰.
    线程安全. 适用于单实例部署; 多实例需换 Redis 等共享存储.
    """

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._buckets: OrderedDict[str, list] = OrderedDict()
        self._lock = threading.Lock()
        # 限制 _buckets 总大小, 防止 IP 爆炸式增长耗内存
        self._max_ips = 10_000

    def _evict_ips(self) -> None:
        """淘汰空 bucket, 控制 _buckets 大小."""
        while len(self._buckets) > self._max_ips:
            self._buckets.popitem(last=False)

    def check(self, ip: str) -> bool:
        """返回 True 表示允许, False 表示被限流."""
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            bucket = self._buckets.get(ip)
            if bucket is None:
                self._buckets[ip] = [now]
                self._evict_ips()
                return True
            # 淘汰窗口外记录
            while bucket and bucket[0] < cutoff:
                bucket.pop(0)
            if len(bucket) >= self.max_calls:
                return False
            bucket.append(now)
            return True


# 鉴权接口限流: 每 IP 每分钟 10 次
_auth_limiter = IPRateLimiter(max_calls=10, window_seconds=60)


def auth_rate_limit(request: Request) -> None:
    """鉴权接口限流依赖."""
    client_ip = request.client.host if request.client else "unknown"
    if not _auth_limiter.check(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁, 请稍后再试.",
            headers={"Retry-After": "60"},
        )


# 向后兼容导出
AdminAuthDep = Depends(require_admin)
