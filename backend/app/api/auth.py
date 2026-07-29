"""/api/auth 端点 - 管理员密码验证, 签发签名 token."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..schemas import VerifyOut
from ..security import _resolve_admin_credentials, auth_rate_limit, issue_admin_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class VerifyIn(BaseModel):
    password: str = ""


@router.post("/verify", response_model=VerifyOut)
def verify_password(
    payload: VerifyIn,
    _rl: None = Depends(auth_rate_limit),
) -> VerifyOut:
    """验证管理员密码, 成功则签发签名 token.

    - 未配置凭证 (ADMIN_PASSWORD_HASH/ADMIN_PASSWORD 均为空): 返回 ok=False, 提示需配置
    - 密码正确: 返回 ok=True + token (前端需在后续敏感请求中携带 Authorization: Bearer <token>)
    - 密码错误: 返回 ok=False (配合 IP 限流防暴力破解)
    """
    token = issue_admin_token(payload.password)
    if token is None:
        if _resolve_admin_credentials() is None:
            return VerifyOut(ok=False, message="未配置管理员凭证, 请在 .env 设置 ADMIN_PASSWORD_HASH")
        return VerifyOut(ok=False, message="密码错误")
    return VerifyOut(ok=True, message="验证通过", token=token)
