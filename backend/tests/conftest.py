"""pytest 公共夹具 - 内存 SQLite 数据库, 隔离测试."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
import app.models  # noqa: F401  确保所有模型被注册


@pytest.fixture()
def db_session() -> Session:
    """每个测试用例独立的内存 SQLite 会话, 自动建表 + 测试后清理."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def today() -> date:
    """固定今天日期, 保证测试可重复."""
    return date(2026, 7, 30)
