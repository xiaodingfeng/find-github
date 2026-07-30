"""GitHub Search API 客户端 - 异步, 分页, 节流, 重试.

核心思路 (trending 模式):
1. 用 `/search/repositories` 配合 `stars:>{threshold} sort:stars-desc` 获取候选仓库
   (总 star 数超过阈值的仓库才可能在 period 内新增超过阈值 star)
2. 对每个候选仓库, 调用 GraphQL API (stargazers + orderBy:DESC) 从最新 star
   开始向前分页, 统计 period 内新增的 star 数. (REST API 受 10,000 条分页上限限制,
   无法处理高 star 仓库, 故改用 GraphQL)
3. 过滤出 stars_gained > threshold 的仓库, 即为 trending 榜单
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from ..config import settings
from ..tz import CST

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
SEARCH_REPOS_PATH = "/search/repositories"
GITHUB_TRENDING_URL = "https://github.com/trending"

# 已认证: Search 30 req/min, Core 5000 req/hour. 取保守间隔
SEARCH_REQUEST_INTERVAL = 2.0   # Search API: 30 req/min
STARGAZER_REQUEST_INTERVAL = 1.0  # Core API: 可达 60 req/min
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 5.0

# GraphQL 单次 stargazers 查询的 edges 数量上限 (1-100)
GRAPHQL_PAGE_SIZE = 100
# 单仓库最多翻多少页 GraphQL (防止极端仓库无限翻页)
GRAPHQL_MAX_PAGES_PER_REPO = 200

# Top 语言列表, 用于分语言迭代查询突破 1000 条上限
DEFAULT_TOP_LANGUAGES = [
    "Python", "JavaScript", "TypeScript", "Java", "Go",
    "Rust", "C++", "C", "C#", "PHP",
    "Ruby", "Swift", "Kotlin", "Dart", "Scala",
    "Shell", "HTML", "CSS", "Lua", "Elixir",
    "Haskell", "Julia", "Zig", "Nim", "OCaml",
]

# 低基数新秀查询的 star 阈值: 用于捞取低 star 但高增长的新仓库
# (默认 sort=stars 会让 50★ 的新秀排在 10000★ 老仓库之后, 进不了前 1000)
LOW_BASE_THRESHOLD = 3


@dataclass
class CrawlResult:
    """一次抓取的汇总结果."""

    period: str
    threshold: int
    items: List[Dict[str, Any]] = field(default_factory=list)
    seen_github_ids: set = field(default_factory=set)
    total_found: int = 0
    queries_made: int = 0
    # github_id -> period 内新增 star 数
    stars_gained_map: Dict[int, int] = field(default_factory=dict)

    def add_items(self, items: List[Dict[str, Any]]) -> int:
        """添加抓取的仓库, 去重. 返回新增数量."""
        added = 0
        for it in items:
            gid = it.get("id")
            if gid is None or gid in self.seen_github_ids:
                continue
            self.seen_github_ids.add(gid)
            self.items.append(it)
            added += 1
        return added


def compute_since_date(period: str, today: Optional[date] = None) -> date:
    """根据 period 计算起始日期 (基于东八区今天)."""
    from ..tz import now_cn
    today = today or now_cn().date()
    if period == "daily":
        return today - timedelta(days=1)
    if period == "weekly":
        return today - timedelta(days=7)
    if period == "monthly":
        return today - timedelta(days=30)
    raise ValueError(f"Unknown period: {period}. Must be daily/weekly/monthly.")


def build_candidate_query(
    threshold: int,
    language: Optional[str] = None,
    since: Optional[date] = None,
    mode: str = "created",
) -> str:
    """构造候选仓库查询字符串.

    Args:
        threshold: 总 star 下限
        language: 限定语言
        since: 起始日期, 配合 mode 过滤 period 内仓库
        mode: "created" = period 内创建的新仓库 (新秀榜);
              "pushed"  = period 内有 push 的活跃仓库 (活跃榜);
              "none"    = 不加时间过滤 (仅按 star 排序, 旧逻辑)
    """
    q = f"stars:>{threshold}"
    if language:
        q += f" language:{language}"
    if since and mode == "created":
        q += f" created:>={since.isoformat()}"
    elif since and mode == "pushed":
        q += f" pushed:>={since.isoformat()}"
    return q


class GitHubSearchClient:
    """GitHub API 异步客户端 - Search + Stargazers."""

    def __init__(
        self,
        token: Optional[str] = None,
        per_page: int = 100,
        max_pages: Optional[int] = None,
    ) -> None:
        self.token = (token or settings.GITHUB_TOKEN or "").strip()
        self.per_page = min(max(per_page, 1), 100)
        self.max_pages = max_pages or settings.GITHUB_CRAWL_MAX_PAGES_PER_QUERY
        self._last_search_at: float = 0.0
        self._last_stargazer_at: float = 0.0
        # GraphQL stargazers 是否被 token 拒绝 (fine-grained PAT). 缓存后直接走 REST
        self._graphql_forbidden: bool = False

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "find-github-crawler/0.1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._headers = headers
        # stargazers 请求需要特殊 Accept 头以获取 starred_at
        self._stargazer_headers = {
            **headers,
            "Accept": "application/vnd.github.star+json",
        }

    async def _throttle_search(self) -> None:
        now = asyncio.get_event_loop().time()
        wait = SEARCH_REQUEST_INTERVAL - (now - self._last_search_at)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_search_at = asyncio.get_event_loop().time()

    async def _throttle_stargazer(self) -> None:
        now = asyncio.get_event_loop().time()
        wait = STARGAZER_REQUEST_INTERVAL - (now - self._last_stargazer_at)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_stargazer_at = asyncio.get_event_loop().time()

    async def _request_with_retry(
        self, client: httpx.AsyncClient, url: str, params: Dict[str, Any], headers: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """带重试的 GET 请求. 返回 None 表示彻底失败."""
        is_search = "/search/" in url
        for attempt in range(1, MAX_RETRIES + 1):
            if is_search:
                await self._throttle_search()
            else:
                await self._throttle_stargazer()
            try:
                resp = await client.get(url, params=params, headers=headers, timeout=30.0)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (403, 429):
                    wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        "GitHub API rate limited (status=%s url=%s). Retry %d/%d after %.1fs",
                        resp.status_code, url, attempt, MAX_RETRIES, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code == 409:
                    # Empty repo (no stargazers) — 正常情况, 返回空
                    logger.debug("409 for %s (likely empty repo)", url)
                    return {"items": []}
                if resp.status_code == 404:
                    logger.debug("404 for %s (repo deleted or renamed)", url)
                    return None
                logger.error(
                    "GitHub API error: status=%s url=%s body=%s",
                    resp.status_code, url, resp.text[:500],
                )
                return None
            except (httpx.RequestError, httpx.HTTPError) as e:
                wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "Request error: %s url=%s. Retry %d/%d after %.1fs",
                    e, url, attempt, MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
        logger.error("All retries exhausted for url=%s params=%s", url, params)
        return None

    async def search_one_query(
        self,
        client: httpx.AsyncClient,
        query: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """对单个 search query 分页拉取所有结果 (受 max_pages 限制).

        Args:
            cancel_check: 可选的取消检查回调, 返回 True 时立即抛出 RuntimeError 中止抓取
        """
        total_fetched = 0
        for page in range(1, self.max_pages + 1):
            # 取消检查点: 在每页请求前检查
            if cancel_check is not None and cancel_check():
                raise RuntimeError("用户手动停止抓取")

            params = {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": self.per_page,
                "page": page,
            }
            data = await self._request_with_retry(
                client, f"{GITHUB_API_BASE}{SEARCH_REPOS_PATH}", params, self._headers
            )
            if data is None:
                break

            items = data.get("items", [])
            total_count = data.get("total_count", 0)

            if not items:
                break

            for it in items:
                yield it
                total_fetched += 1

            if total_fetched >= total_count:
                break
            if len(items) < self.per_page:
                break
            if total_fetched >= 1000:
                break

    async def _graphql_request_with_retry(
        self,
        client: httpx.AsyncClient,
        query: str,
        variables: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """带重试的 GraphQL POST 请求.

        Returns:
            (data, forbidden): data 为响应数据 (None 表示失败),
            forbidden=True 表示 token 不支持该资源 (FORBIDDEN), 不应重试.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            await self._throttle_stargazer()
            try:
                resp = await client.post(
                    GITHUB_GRAPHQL_URL,
                    json={"query": query, "variables": variables},
                    headers=self._headers,
                    timeout=30.0,
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    if "errors" in payload and payload["errors"]:
                        err_msg = str(payload["errors"][0].get("message", ""))
                        err_type = payload["errors"][0].get("type", "")
                        # FORBIDDEN: fine-grained PAT 不支持, 不重试
                        if err_type == "FORBIDDEN" or "FORBIDDEN" in err_msg.upper():
                            return None, True
                        if "rate limit" in err_msg.lower() or "secondary rate" in err_msg.lower():
                            wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                            logger.warning(
                                "GraphQL rate limited: %s. Retry %d/%d after %.1fs",
                                err_msg, attempt, MAX_RETRIES, wait,
                            )
                            await asyncio.sleep(wait)
                            continue
                        logger.error("GraphQL errors: %s", payload["errors"])
                        return None, False
                    return payload.get("data"), False
                if resp.status_code in (403, 429):
                    # HTTP 403 也可能是 token 权限问题, 标记 forbidden
                    if resp.status_code == 403:
                        return None, True
                    wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        "GraphQL HTTP %s. Retry %d/%d after %.1fs",
                        resp.status_code, attempt, MAX_RETRIES, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    "GraphQL HTTP error: status=%s body=%s",
                    resp.status_code, resp.text[:500],
                )
                return None, False
            except (httpx.RequestError, httpx.HTTPError) as e:
                wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "GraphQL request error: %s. Retry %d/%d after %.1fs",
                    e, attempt, MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
        logger.error("GraphQL all retries exhausted. variables=%s", variables)
        return None, False

    async def _count_stars_since_graphql(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        since: date,
    ) -> Optional[int]:
        """GraphQL 方式统计 stars_gained. 返回 None 表示 token 不支持 (FORBIDDEN)."""
        since_dt = datetime.combine(since, datetime.min.time(), tzinfo=CST)
        count = 0
        after_cursor: Optional[str] = None

        graphql_query = """
        query ($owner: String!, $name: String!, $first: Int!, $after: String) {
          repository(owner: $owner, name: $name) {
            stargazers(first: $first, after: $after, orderBy: {field: STARRED_AT, direction: DESC}) {
              edges {
                starredAt
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
        """

        forbidden_logged = False
        for page_idx in range(GRAPHQL_MAX_PAGES_PER_REPO):
            variables = {
                "owner": owner,
                "name": repo,
                "first": GRAPHQL_PAGE_SIZE,
                "after": after_cursor,
            }
            data, forbidden = await self._graphql_request_with_retry(
                client, graphql_query, variables
            )
            if forbidden:
                if not forbidden_logged:
                    logger.warning(
                        "GraphQL stargazers FORBIDDEN for %s/%s. "
                        "Fine-grained PAT 不支持, 请改用 Classic Token (ghp_). "
                        "将回退到 REST API (仅对 star<=10000 的仓库有效).",
                        owner, repo,
                    )
                return None
            if data is None:
                break

            repo_data = data.get("repository")
            if not repo_data:
                break

            sg = repo_data.get("stargazers") or {}
            edges = sg.get("edges") or []
            if not edges:
                break

            page_count = 0
            all_recent = True
            for edge in edges:
                starred_at_str = edge.get("starredAt", "")
                try:
                    starred_at = datetime.fromisoformat(
                        starred_at_str.replace("Z", "+00:00")
                    )
                    if starred_at >= since_dt:
                        page_count += 1
                    else:
                        all_recent = False
                        break
                except (ValueError, TypeError):
                    continue

            count += page_count
            if not all_recent:
                break

            page_info = sg.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after_cursor = page_info.get("endCursor")
            if not after_cursor:
                break

        return count

    async def _count_stars_since_rest(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        since: date,
        total_stars: int,
    ) -> int:
        """REST API 回退方案: 从最后一页倒序遍历 stargazers.

        限制: REST API 最多访问 100 页 (10000 条), 对 star>10000 的仓库
        无法获取最新 star, 此时会返回 0 并记录警告.
        """
        if total_stars <= 0:
            return 0

        import math
        total_pages = math.ceil(total_stars / self.per_page)
        # REST API 最多支持 100 页
        max_accessible_page = min(total_pages, 100)

        # 如果仓库 star > 10000, 最新 star 在不可访问的高页码
        if total_pages > 100:
            logger.warning(
                "REST fallback: %s/%s has %d stars (>10000), "
                "cannot access recent stars via REST API. Skipping. "
                "Use Classic Token for GraphQL support.",
                owner, repo, total_stars,
            )
            return 0

        since_dt = datetime.combine(since, datetime.min.time(), tzinfo=CST)
        count = 0

        for page in range(max_accessible_page, 0, -1):
            params = {"per_page": self.per_page, "page": page}
            url = f"{GITHUB_API_BASE}/repos/{quote(owner)}/{quote(repo)}/stargazers"
            data = await self._request_with_retry(client, url, params, self._stargazer_headers)
            if data is None:
                break

            entries = data if isinstance(data, list) else data.get("items", [])
            if not entries:
                break

            page_count = 0
            all_recent = True
            for entry in entries:
                starred_at_str = entry.get("starred_at", "")
                try:
                    starred_at = datetime.fromisoformat(
                        starred_at_str.replace("Z", "+00:00")
                    )
                    if starred_at >= since_dt:
                        page_count += 1
                    else:
                        all_recent = False
                except (ValueError, TypeError):
                    continue

            count += page_count
            if not all_recent:
                break

        return count

    async def count_stars_since(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        since: date,
        total_stars: int,
    ) -> int:
        """统计 repo 在 since 日期之后新增的 star 数.

        策略: 优先 GraphQL (支持高 star 仓库, 需要 Classic Token),
        若 token 不支持则回退 REST API (仅 star<=10000 有效).
        """
        if total_stars <= 0:
            return 0

        # 若 GraphQL 已被 token 拒绝, 直接走 REST
        if not self._graphql_forbidden:
            graphql_result = await self._count_stars_since_graphql(client, owner, repo, since)
            if graphql_result is not None:
                return graphql_result
            # GraphQL FORBIDDEN, 标记后后续直接走 REST
            self._graphql_forbidden = True

        # 回退 REST
        return await self._count_stars_since_rest(client, owner, repo, since, total_stars)

    async def get_user_info(
        self,
        client: httpx.AsyncClient,
        owner: str,
    ) -> Optional[Dict[str, Any]]:
        """查询 GitHub user API 获取 owner 信息 (location/company/bio). 用于地区检测."""
        url = f"{GITHUB_API_BASE}/users/{quote(owner)}"
        data = await self._request_with_retry(client, url, {}, self._headers)
        return data

    async def get_repo_info(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
    ) -> Optional[Dict[str, Any]]:
        """查询单个仓库的最新信息 (用于历史高分池刷新).

        返回格式与 search/repositories 的 item 一致, 可直接传给 _upsert_repository.
        """
        url = f"{GITHUB_API_BASE}/repos/{quote(owner)}/{quote(repo)}"
        data = await self._request_with_retry(client, url, {}, self._headers)
        return data

    async def fetch_trending_page(
        self,
        client: httpx.AsyncClient,
        period: str,
        language: str = "",
    ) -> List[Dict[str, Any]]:
        """直接抓取 GitHub Trending 页面 HTML, 解析仓库列表.

        这是获取真实 trending 榜单的唯一可靠方式 (Search API 不支持按 star 增长排序).
        页面提供: 仓库名、描述、语言、总 star、fork、period 内新增 star.

        Args:
            period: daily / weekly / monthly
            language: 可选语言过滤 (如 python), 空字符串表示全部

        Returns:
            仓库字典列表, 每个包含 full_name, stars_gained, total_stars, language, description
        """
        params = {"since": period}
        if language:
            params["language"] = language
        try:
            resp = await client.get(
                GITHUB_TRENDING_URL,
                params=params,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
                timeout=30.0,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Trending page returned status=%s for period=%s lang=%s",
                    resp.status_code, period, language or "all",
                )
                return []
            html = resp.text
        except Exception as e:
            logger.exception("Failed to fetch trending page: %s", e)
            return []

        # 解析 HTML: 每个 <article> 包含一个仓库
        # 提取: href="/owner/repo", stars today/this week/this month, 总 star, 语言, 描述
        results = []

        # 匹配仓库 article 块
        # <article class="Box-row"> ... <h2><a href="/owner/repo">...</a></h2> ... </article>
        article_pattern = re.compile(
            r'<article[^>]*class="[^"]*Box-row[^"]*"[^>]*>(.*?)</article>',
            re.DOTALL,
        )
        # 提取仓库路径
        href_pattern = re.compile(r'<h2[^>]*>\s*<a[^>]*href="(/[^"]+)"', re.DOTALL)
        # 提取 stars gained: "2,506 stars today" 或 "1,234 stars this week" 或 "567 stars this month"
        gained_pattern = re.compile(
            r'([\d,]+)\s+stars?\s+(today|this week|this month)',
            re.IGNORECASE,
        )
        # 提取总 star: href="/owner/repo/stargazers">  11,637
        stars_pattern = re.compile(
            r'href="/[^"]+/stargazers"[^>]*>\s*([\d,]+)\s*</a>',
            re.DOTALL,
        )
        # 提取语言
        lang_pattern = re.compile(
            r'<span[^>]*itemprop="programmingLanguage"[^>]*>\s*([^<]+)\s*</span>',
        )
        # 提取描述
        desc_pattern = re.compile(r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>\s*(.*?)\s*</p>', re.DOTALL)

        for match in article_pattern.finditer(html):
            block = match.group(1)
            href_match = href_pattern.search(block)
            if not href_match:
                continue
            path = href_match.group(1).strip("/")
            if "/" not in path:
                continue

            # stars_gained
            gained = 0
            gained_match = gained_pattern.search(block)
            if gained_match:
                gained = int(gained_match.group(1).replace(",", ""))

            # 总 star
            total_stars = 0
            stars_match = stars_pattern.search(block)
            if stars_match:
                total_stars = int(stars_match.group(1).replace(",", ""))

            # 语言
            lang = ""
            lang_match = lang_pattern.search(block)
            if lang_match:
                lang = lang_match.group(1).strip()

            # 描述
            desc = ""
            desc_match = desc_pattern.search(block)
            if desc_match:
                desc = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip()

            results.append({
                "full_name": path,
                "owner": path.split("/")[0],
                "name": path.split("/", 1)[1],
                "stars_gained": gained,
                "total_stars": total_stars,
                "language": lang,
                "description": desc,
                "_source": "trending_page",
            })

        logger.info(
            "Trending page: period=%s lang=%s → %d repos",
            period, language or "all", len(results),
        )
        return results

    async def fetch_trending_repos_detail(
        self,
        client: httpx.AsyncClient,
        trending_items: List[Dict[str, Any]],
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """对 Trending 页面的仓库批量调用 /repos API 获取完整信息.

        将 Trending 页面的基础信息与 API 详情合并后 yield.
        每个返回的 item 格式与 search/repositories 一致, 可直接传给 _upsert_repository.
        """
        for item in trending_items:
            if cancel_check is not None and cancel_check():
                raise RuntimeError("用户手动停止抓取")
            full_name = item["full_name"]
            owner, repo = full_name.split("/", 1)
            page_gained = item.get("stars_gained", 0)
            # 调用 /repos API 获取完整信息
            api_data = await self.get_repo_info(client, owner, repo)
            if api_data and "id" in api_data:
                # 注入 Trending 页面提供的 stars_gained (真实增量, 非估算)
                if page_gained > 0:
                    api_data["_stars_gained_from_page"] = page_gained
                yield api_data
            else:
                # API 失败时降级: 用 Trending 页面的基础信息构造最小 item
                logger.warning("API failed for %s, using trending page data only", full_name)
                # N2: 用稳定的 md5 哈希替代内置 hash (内置 hash 受 PYTHONHASHSEED 影响, 跨进程不稳定)
                import hashlib

                pseudo_id = int(hashlib.md5(full_name.encode("utf-8")).hexdigest()[:8], 16)
                yield {
                    "id": pseudo_id,
                    "name": item["name"],
                    "full_name": full_name,
                    "owner": {"login": owner, "type": "User"},
                    "html_url": f"https://github.com/{full_name}",
                    "description": item.get("description"),
                    "language": item.get("language") or None,
                    "topics": [],
                    "license": None,
                    "stargazers_count": item.get("total_stars", 0),
                    "forks_count": 0,
                    "watchers_count": 0,
                    "open_issues_count": 0,
                    "created_at": None,
                    "updated_at": None,
                    "pushed_at": None,
                    "_stars_gained_from_page": item.get("stars_gained", 0),
                }

    async def crawl_period(
        self,
        period: str,
        threshold: Optional[int] = None,
        languages: Optional[List[str]] = None,
        today: Optional[date] = None,
        max_candidates: Optional[int] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> CrawlResult:
        """抓取指定 period 的候选仓库 (贴近 GitHub Trending 语义).

        查询策略与 crawl_period_iter 一致:
        1. 新秀榜 (created >= since): period 内创建的新仓库
        2. 活跃榜 (pushed >= since): period 内有 push 的活跃仓库
        3. 按语言迭代查询, 突破 1000 条上限

        stars_gained 的计算由 tasks.py 通过快照差值法完成.

        Args:
            period: daily / weekly / monthly
            threshold: star 阈值, 默认从 settings 读取
            languages: 按语言迭代查询, None 时根据 settings 决定
            today: 用于测试注入日期
            max_candidates: 最多保留多少个候选仓库 (默认从 settings 读取)
            cancel_check: 可选的取消检查回调, 返回 True 时立即抛出 RuntimeError 中止抓取
        """
        threshold = threshold if threshold is not None else settings.GITHUB_CRAWL_THRESHOLD
        max_candidates = max_candidates or settings.GITHUB_CRAWL_MAX_CANDIDATES
        since = compute_since_date(period, today)

        # 决定是否按语言拆分
        if languages is None:
            top_n = settings.GITHUB_CRAWL_TOP_LANGUAGES
            languages = DEFAULT_TOP_LANGUAGES[:top_n] if top_n > 0 else [None]
        else:
            languages = list(languages) if languages else [None]

        result = CrawlResult(period=period, threshold=threshold)

        async with httpx.AsyncClient() as client:
            logger.info(
                "Collecting candidates period=%s since=%s threshold=%d",
                period, since, threshold,
            )
            # 进入搜索阶段前先检查取消信号
            if cancel_check is not None and cancel_check():
                raise RuntimeError("用户手动停止抓取")

            # ===== Phase 1: 新秀榜 (created >= since) =====
            # monthly 跳过 created 维度 (30 天前创建的多为低 star 小仓库, 配额性价比低)
            skip_created = period == "monthly"
            if not skip_created:
                logger.info("Phase 1: new repos created since %s", since)
                base_new_query = build_candidate_query(threshold, language=None, since=since, mode="created")
                result.queries_made += 1
                async for item in self.search_one_query(client, base_new_query, cancel_check=cancel_check):
                    result.add_items([item])

                # 低基数新秀榜: 降低 star 阈值, 捞取低 star 但高增长的新仓库
                if threshold > LOW_BASE_THRESHOLD:
                    low_base_query = build_candidate_query(
                        LOW_BASE_THRESHOLD, language=None, since=since, mode="created"
                    )
                    result.queries_made += 1
                    async for item in self.search_one_query(client, low_base_query, cancel_check=cancel_check):
                        result.add_items([item])

                for lang in languages:
                    if lang is None:
                        continue
                    if cancel_check is not None and cancel_check():
                        raise RuntimeError("用户手动停止抓取")
                    lang_new_query = build_candidate_query(threshold, language=lang, since=since, mode="created")
                    logger.info(
                        "Phase 1 lang=%s (candidates so far: %d)",
                        lang, len(result.items),
                    )
                    result.queries_made += 1
                    async for item in self.search_one_query(client, lang_new_query, cancel_check=cancel_check):
                        result.add_items([item])
            else:
                logger.info("Phase 1 skipped: monthly period, created dimension skipped")

            # ===== Phase 2: 活跃榜 (pushed >= since) =====
            logger.info("Phase 2: active repos pushed since %s", since)
            base_active_query = build_candidate_query(threshold, language=None, since=since, mode="pushed")
            result.queries_made += 1
            async for item in self.search_one_query(client, base_active_query, cancel_check=cancel_check):
                result.add_items([item])

            for lang in languages:
                if lang is None:
                    continue
                if cancel_check is not None and cancel_check():
                    raise RuntimeError("用户手动停止抓取")
                lang_active_query = build_candidate_query(threshold, language=lang, since=since, mode="pushed")
                logger.info(
                    "Phase 2 lang=%s (candidates so far: %d)",
                    lang, len(result.items),
                )
                result.queries_made += 1
                async for item in self.search_one_query(client, lang_active_query, cancel_check=cancel_check):
                    result.add_items([item])

            total_candidates = len(result.items)
            logger.info("Phases done: %d unique candidates", total_candidates)

            # 限制候选数量: 按总 star 降序取前 N 个
            result.items.sort(
                key=lambda x: x.get("stargazers_count", 0), reverse=True
            )
            if len(result.items) > max_candidates:
                logger.info(
                    "Trimming candidates from %d to %d (max_candidates)",
                    len(result.items), max_candidates,
                )
                result.items = result.items[:max_candidates]
                result.seen_github_ids = {it["id"] for it in result.items}

            result.total_found = len(result.items)
            logger.info(
                "Crawl done. period=%s threshold=%d queries=%d candidates=%d",
                period, threshold, result.queries_made, result.total_found,
            )
        return result

    async def crawl_period_iter(
        self,
        period: str,
        threshold: Optional[int] = None,
        languages: Optional[List[str]] = None,
        today: Optional[date] = None,
        max_candidates: Optional[int] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """流式抓取: 边搜索边 yield 仓库, 供调用方逐条入库.

        查询策略:
        1. Phase 1 (Trending 页面): 直接抓取 github.com/trending HTML
           - 100% 匹配 GitHub Trending 真实榜单
           - 提供 stars_gained (period 内真实增量, 非估算)
           - 但仅 Top 25 个仓库
        2. Phase 2 (Search API 补充): 用 created/pushed 过滤获取更多候选
           - 补充 Trending 页面之外的高增长仓库
           - 按语言迭代突破 1000 条限制

        Args:
            period: daily / weekly / monthly
            threshold: star 阈值, 默认从 settings 读取
            languages: 按语言迭代查询, None 时根据 settings 决定
            today: 用于测试注入日期
            max_candidates: 最多保留多少个候选仓库 (默认从 settings 读取)
            cancel_check: 可选的取消检查回调, 返回 True 时立即抛出 RuntimeError
        """
        threshold = threshold if threshold is not None else settings.GITHUB_CRAWL_THRESHOLD
        max_candidates = max_candidates or settings.GITHUB_CRAWL_MAX_CANDIDATES
        since = compute_since_date(period, today)

        # 决定是否按语言拆分 (Phase 2 用)
        if languages is None:
            top_n = settings.GITHUB_CRAWL_TOP_LANGUAGES
            languages = DEFAULT_TOP_LANGUAGES[:top_n] if top_n > 0 else [None]
        else:
            languages = list(languages) if languages else [None]

        seen_ids: set = set()
        count = 0

        async with httpx.AsyncClient() as client:
            logger.info(
                "Streaming crawl: period=%s threshold=%d since=%s",
                period, threshold, since,
            )

            # 进入搜索前检查取消
            if cancel_check is not None and cancel_check():
                raise RuntimeError("用户手动停止抓取")

            # ===== Phase 1: 直接抓取 GitHub Trending 页面 (100% 匹配真实榜单) =====
            logger.info("Phase 1: fetching GitHub Trending page (period=%s)", period)
            trending_items = await self.fetch_trending_page(client, period, language="")

            # 对每个 Trending 仓库调用 /repos API 获取完整信息
            async for item in self.fetch_trending_repos_detail(
                client, trending_items, cancel_check=cancel_check
            ):
                gid = item.get("id")
                if gid is None or gid in seen_ids:
                    continue
                seen_ids.add(gid)
                yield item
                count += 1
                if count >= max_candidates:
                    logger.info("Reached max_candidates=%d (phase 1 trending), stopping", max_candidates)
                    return

            logger.info("Phase 1 done: %d repos from Trending page", count)

            # ===== Phase 2: Search API 补充 (获取更多候选仓库) =====
            # 新秀榜 (created >= since): period 内创建的新仓库
            # monthly 跳过 created 维度 (30 天前创建的多为低 star 小仓库, 配额性价比低)
            skip_created = period == "monthly"
            logger.info(
                "Phase 2: Search API supplement (created=%s, pushed since %s)",
                "skip" if skip_created else "on", since,
            )
            if not skip_created:
                base_new_query = build_candidate_query(threshold, language=None, since=since, mode="created")
                async for item in self.search_one_query(client, base_new_query, cancel_check=cancel_check):
                    gid = item.get("id")
                    if gid is None or gid in seen_ids:
                        continue
                    seen_ids.add(gid)
                    yield item
                    count += 1
                    if count >= max_candidates:
                        logger.info("Reached max_candidates=%d (phase 2 created), stopping", max_candidates)
                        return

                # 低基数新秀榜: 降低 star 阈值, 捞取低 star 但高增长的新仓库
                # (默认 sort=stars 会让 50★ 新秀排在 10000★ 老仓库之后, 进不了前 1000)
                if threshold > LOW_BASE_THRESHOLD:
                    low_base_query = build_candidate_query(
                        LOW_BASE_THRESHOLD, language=None, since=since, mode="created"
                    )
                    async for item in self.search_one_query(client, low_base_query, cancel_check=cancel_check):
                        gid = item.get("id")
                        if gid is None or gid in seen_ids:
                            continue
                        seen_ids.add(gid)
                        yield item
                        count += 1
                        if count >= max_candidates:
                            logger.info("Reached max_candidates=%d (phase 2 low-base), stopping", max_candidates)
                            return

            # 活跃榜 (pushed >= since): period 内有 push 的活跃仓库
            base_active_query = build_candidate_query(threshold, language=None, since=since, mode="pushed")
            async for item in self.search_one_query(client, base_active_query, cancel_check=cancel_check):
                gid = item.get("id")
                if gid is None or gid in seen_ids:
                    continue
                seen_ids.add(gid)
                yield item
                count += 1
                if count >= max_candidates:
                    logger.info("Reached max_candidates=%d (phase 2 pushed), stopping", max_candidates)
                    return

            # 按语言迭代查询 (突破 1000 条限制)
            # 每个 lang 同时查 created + pushed, 覆盖非主流语言的活跃老仓库
            for lang in languages:
                if lang is None:
                    continue
                if cancel_check is not None and cancel_check():
                    raise RuntimeError("用户手动停止抓取")
                logger.info("Phase 2 lang=%s (yielded so far: %d)", lang, count)
                # 按语言 created (monthly 跳过)
                if not skip_created:
                    lang_new_query = build_candidate_query(threshold, language=lang, since=since, mode="created")
                    async for item in self.search_one_query(client, lang_new_query, cancel_check=cancel_check):
                        gid = item.get("id")
                        if gid is None or gid in seen_ids:
                            continue
                        seen_ids.add(gid)
                        yield item
                        count += 1
                        if count >= max_candidates:
                            logger.info("Reached max_candidates=%d (phase 2 lang created), stopping", max_candidates)
                            return
                # 按语言 pushed (新增: 覆盖非主流语言的活跃老仓库)
                lang_push_query = build_candidate_query(threshold, language=lang, since=since, mode="pushed")
                async for item in self.search_one_query(client, lang_push_query, cancel_check=cancel_check):
                    gid = item.get("id")
                    if gid is None or gid in seen_ids:
                        continue
                    seen_ids.add(gid)
                    yield item
                    count += 1
                    if count >= max_candidates:
                        logger.info("Reached max_candidates=%d (phase 2 lang pushed), stopping", max_candidates)
                        return

        logger.info("Streaming crawl done: %d unique candidates yielded", count)
