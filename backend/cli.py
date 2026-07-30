"""CLI 入口: python -m cli crawl --period daily [--threshold 100]
            python -m cli initdb
            python -m cli server
            python -m cli archive-snapshots [--retention-days 90]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 让脚本可独立运行 (python -m cli)
sys.path.insert(0, str(Path(__file__).resolve().parent))


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_initdb(args: argparse.Namespace) -> int:
    from app.database import init_db

    print("Creating database tables...")
    init_db()
    print("Done.")
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    from app.crawler.tasks import crawl_period

    summary = crawl_period(
        period=args.period,
        threshold=args.threshold,
    )
    print(
        f"\nCrawl finished. run_id={summary.run_id} period={summary.period} "
        f"status={summary.status} found={summary.total_repos_found} "
        f"upserted={summary.total_repos_upserted}"
    )
    if summary.error_message:
        print(f"error: {summary.error_message}")
    return 0 if summary.status == "success" else 1


def cmd_server(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def cmd_interpret(args: argparse.Namespace) -> int:
    """生成 AI 中文解读."""
    from app.crawler.interpreter import interpret_repos

    result = interpret_repos(
        repo_ids=None,
        limit=args.limit,
        force=args.force,
    )
    print(
        f"\nInterpretation finished. total={result['total']} "
        f"success={result['success']} failed={result['failed']}"
    )
    return 0 if result["failed"] == 0 else 1


def cmd_reclassify(args: argparse.Namespace) -> int:
    """用最新规则重新分类所有已入库的仓库 (技术分类 / 效率工具 / 中文文档)."""
    from app.database import SessionLocal
    from app.models import Repository
    from app.crawler.classifier import (
        classify_category,
        detect_chinese_doc,
        detect_efficiency_tool,
    )

    db = SessionLocal()
    try:
        repos = db.query(Repository).all()
        total = len(repos)
        updated = 0
        eff_count = 0
        for r in repos:
            item = {
                "name": r.name,
                "full_name": r.full_name,
                "description": r.description or "",
                "language": r.language or "",
                "topics": r.topics or [],
            }
            category = classify_category(item)
            is_eff, industry = detect_efficiency_tool(item)
            has_cn_doc = detect_chinese_doc(item)

            changed = False
            if category:
                if r.category != category:
                    r.category = category
                    changed = True
            if is_eff:
                if not r.is_efficiency_tool:
                    r.is_efficiency_tool = True
                    changed = True
                if industry and r.industry != industry:
                    r.industry = industry
                    changed = True
                eff_count += 1
            if has_cn_doc and not r.has_chinese_doc:
                r.has_chinese_doc = True
                changed = True
            if changed:
                updated += 1
        db.commit()
        print(f"\nReclassify finished. total={total} updated={updated} efficiency_tools={eff_count}")
        return 0
    finally:
        db.close()


def cmd_archive(args: argparse.Namespace) -> int:
    """归档超期快照: 将超过保留期的明细聚合成月度摘要后删除."""
    from app.crawler.tasks import archive_old_snapshots

    result = archive_old_snapshots(retention_days=args.retention_days)
    print(
        f"\nArchive finished. cutoff={result['cutoff_date']} "
        f"archived_rows={result['archived_rows']} summary_upserted={result['summary_upserted']}"
    )
    return 0


def cmd_hashpw(args: argparse.Namespace) -> int:
    """交互式生成管理员密码哈希, 输出可写入 .env 的 ADMIN_PASSWORD_HASH 值."""
    import getpass

    from app.security import hash_password, verify_password

    pwd = args.password
    if not pwd:
        try:
            pwd = getpass.getpass("请输入管理员密码: ")
            if not pwd:
                print("密码不能为空.")
                return 1
            confirm = getpass.getpass("再次确认密码: ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消.")
            return 1
        if pwd != confirm:
            print("两次输入不一致.")
            return 1
    hashed = hash_password(pwd)
    # 自校验
    if not verify_password(pwd, hashed):
        print("错误: 哈希自校验失败, 请重试.")
        return 1
    print("\n生成成功. 将以下内容写入 backend/.env:\n")
    print(f"ADMIN_PASSWORD_HASH={hashed}")
    print("\n(原 ADMIN_PASSWORD 明文配置可删除)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="find-github",
        description="GitHub 热门仓库定时发现与可视化系统 CLI",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="开启 DEBUG 日志")
    sub = parser.add_subparsers(dest="command", required=True)

    # initdb
    p_initdb = sub.add_parser("initdb", help="创建数据库表")
    p_initdb.set_defaults(func=cmd_initdb)

    # crawl
    p_crawl = sub.add_parser("crawl", help="手动触发一次抓取")
    p_crawl.add_argument(
        "--period", required=True, choices=["daily", "weekly", "monthly"],
        help="抓取时间段",
    )
    p_crawl.add_argument(
        "--threshold", type=int, default=None,
        help="star 阈值 (默认读 settings)",
    )
    p_crawl.set_defaults(func=cmd_crawl)

    # interpret
    p_interp = sub.add_parser("interpret", help="生成 AI 中文解读")
    p_interp.add_argument("--limit", type=int, default=50, help="最多处理仓库数")
    p_interp.add_argument("--force", action="store_true", help="强制重新生成")
    p_interp.set_defaults(func=cmd_interpret)

    # reclassify
    p_reclassify = sub.add_parser("reclassify", help="用最新规则重新分类所有已入库仓库")
    p_reclassify.set_defaults(func=cmd_reclassify)

    # archive-snapshots
    p_archive = sub.add_parser("archive-snapshots", help="归档超期快照 (聚合成月度摘要后删除)")
    p_archive.add_argument(
        "--retention-days", type=int, default=None,
        help="保留天数 (默认读 settings.SNAPSHOT_RETENTION_DAYS=90)",
    )
    p_archive.set_defaults(func=cmd_archive)

    # hashpw
    p_hashpw = sub.add_parser("hashpw", help="生成管理员密码哈希 (ADMIN_PASSWORD_HASH)")
    p_hashpw.add_argument(
        "--password", default=None,
        help="直接传入密码 (不传则交互式输入, 隐藏回显). 交互式更安全.",
    )
    p_hashpw.set_defaults(func=cmd_hashpw)

    # server
    p_server = sub.add_parser("server", help="启动 FastAPI 服务")
    p_server.add_argument("--host", default="0.0.0.0")
    p_server.add_argument("--port", type=int, default=8000)
    p_server.add_argument("--reload", action="store_true")
    p_server.set_defaults(func=cmd_server)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
