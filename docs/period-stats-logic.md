# 每日 / 每周 / 每月统计逻辑详解

本文档说明 find-github 项目中 `daily` / `weekly` / `monthly` 三个统计周期（period）的完整逻辑，包括调度时机、候选仓库抓取、`stars_gained` 计算、热度评分、排名回填，以及前端 `period=all` 聚合去重的行为。

---

## 1. 三个周期的核心定义

| period | 含义 | 时间窗口长度 | since 计算 (`today - N 天`) |
|--------|------|------------|--------------------------|
| `daily` | 每日新增 | 1 天 | `today - 1` |
| `weekly` | 每周新增 | 7 天 | `today - 7` |
| `monthly` | 每月新增 | 30 天 | `today - 30` |

时间窗口长度常量定义在 [tasks.py](file:///d:/dingfeng/work/aiproject/find-github/backend/app/crawler/tasks.py) 中：

```python
PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
```

`since` 起始日期由 [compute_since_date](file:///d:/dingfeng/work/aiproject/find-github/backend/app/crawler/github.py#L81-L91) 计算，基于东八区（Asia/Shanghai）今天。

---

## 2. 调度时机（应用内置调度，默认）

调度配置在 [scheduler.py](file:///d:/dingfeng/work/aiproject/find-github/backend/app/scheduler.py) 中，时区为 `Asia/Shanghai`：

| 任务 | 执行时间 | 说明 |
|------|----------|------|
| `crawl_daily` | 每天 00:00 | 完整抓取，写快照（建立差值基准） |
| `crawl_daily_refresh` | 每天 12:00 | 仅刷新 Repository 表，**不写快照**（避免覆盖 00:00 基准） |
| `crawl_weekly` | 每天 02:00 | 每天写快照 |
| `crawl_monthly` | 每天 04:00 | 每天写快照 |

**关键设计**：
1. weekly / monthly 也是**每天**执行一次，而不是一周/一月才执行一次。这样能保证 `stars_gained` 始终使用精确的快照差值（今天 − 7天前 / 今天 − 30天前），而非估算值。
2. daily 12:00 为**刷新模式**（`write_snapshot=False`）：只更新 Repository 表的最新 star/forks，不写 Snapshot，避免覆盖 00:00 的差值基准导致窗口漂移。

可通过 `SCHEDULER_ENABLED=false` 关闭内置调度，改用系统 crontab 调用 CLI。

---

## 3. 抓取流程（`crawl_period`）

入口函数 [crawl_period](file:///d:/dingfeng/work/aiproject/find-github/backend/app/crawler/tasks.py#L529) 的完整流程：

```
1. 创建 CrawlRun 记录 (status=running)
2. 流式抓取候选仓库 (crawl_period_iter)
   ├─ Phase 1: 抓取 github.com/trending HTML (真实 Top 25, 含真实 stars_gained)
   └─ Phase 2: Search API 补充候选 (created>=since 新秀榜 + pushed>=since 活跃榜, 按语言迭代突破 1000 条上限)
3. 逐条入库:
   a. upsert Repository
   b. 查询 owner 地区 (批量 user API, 识别国产开源)
   c. 计算 stars_gained (见 §4)
   d. 计算热度 score (见 §5)
   e. 写 Snapshot (rank=None, 待回填)
   f. 更新 CrawlRun 进度并 commit
4. Phase 3: 历史高分池兜底 (见 §6)
5. 按 score 降序排名, 回填 rank_in_period
6. 更新 CrawlRun status=success
```

候选仓库总数上限为 `GITHUB_CRAWL_MAX_CANDIDATES=500`（见项目硬约束），star 阈值默认 `GITHUB_CRAWL_THRESHOLD=10`。

支持手动停止：通过 `cancel_event` 在搜索阶段和数据处理阶段响应取消信号。

### 3.1 `crawl_period_iter` 流式抓取伪代码

入口函数 [crawl_period_iter](file:///d:/dingfeng/work/aiproject/find-github/backend/app/crawler/github.py#L810-L929) 是一个异步生成器，边搜索边 `yield` 仓库，供调用方逐条入库。完整伪代码如下：

```
函数 crawl_period_iter(period, threshold, languages, today, max_candidates, cancel_check):
    # ===== 参数初始化 =====
    threshold  = threshold  或 settings.GITHUB_CRAWL_THRESHOLD        # 默认 10
    max_candidates = max_candidates 或 settings.GITHUB_CRAWL_MAX_CANDIDATES  # 默认 500
    since      = compute_since_date(period, today)                     # daily:-1 / weekly:-7 / monthly:-30
    today      = today 或 now_cn().date()                              # 东八区今天

    # 决定按语言拆分列表 (Phase 2 用)
    if languages 为 None:
        top_n = settings.GITHUB_CRAWL_TOP_LANGUAGES                    # 默认 20
        languages = DEFAULT_TOP_LANGUAGES[:top_n]  若 top_n>0 否则 [None]
    else:
        languages = languages 或 [None]

    seen_ids = 空集合        # 全局去重 (跨 Phase), 按 github id 去重
    count = 0               # 已 yield 的候选总数

    async with httpx.AsyncClient() as client:

        # ===== 进入搜索前: 取消检查点 1 =====
        if cancel_check 且 cancel_check() 返回 True:
            raise RuntimeError("用户手动停止抓取")

        # ============================================================
        # Phase 1: 抓取 github.com/trending HTML (100% 匹配真实榜单)
        # ============================================================
        # 查询参数: since=daily/weekly/monthly, language="" (全部语言)
        trending_items = await fetch_trending_page(client, period, language="")
        # 返回 Top 25 仓库的基础信息: full_name, stars_gained(真实增量),
        #                            total_stars, language, description

        # 对每个 Trending 仓库调用 /repos/{owner}/{repo} API 获取完整信息
        async for item in fetch_trending_repos_detail(client, trending_items, cancel_check):
            gid = item["id"]
            if gid 为 None 或 gid ∈ seen_ids:
                continue                  # 跳过无 id 或已 yield 的仓库
            seen_ids.add(gid)
            yield item                    # 流式吐出, 调用方立即入库
            count += 1
            if count >= max_candidates:   # 达到上限提前退出
                return

        # Phase 1 结束: 此时 count 通常为 0~25

        # ============================================================
        # Phase 2: Search API 补充候选 (突破 Trending Top 25 的限制)
        # ============================================================

        # ----- 2.1 新秀榜 (created >= since): period 内创建的新仓库 -----
        # monthly 跳过 created 维度 (30 天前创建的多为低 star 小仓库, 配额性价比低)
        skip_created = (period == "monthly")
        if not skip_created:
            # 查询字符串: "stars:>10 created:>=2026-07-29"
            base_new_query = build_candidate_query(threshold, language=None, since=since, mode="created")
            async for item in search_one_query(client, base_new_query, cancel_check):
                gid = item["id"]
                if gid 为 None 或 gid ∈ seen_ids:
                    continue
                seen_ids.add(gid)
                yield item
                count += 1
                if count >= max_candidates:
                    return

            # ----- 2.1b 低基数新秀榜: 降低 star 阈值, 捞低基数高增长新秀 -----
            # (默认 sort=stars 会让 50★ 新秀排在 10000★ 老仓库之后, 进不了前 1000)
            if threshold > LOW_BASE_THRESHOLD:
                low_base_query = build_candidate_query(LOW_BASE_THRESHOLD, language=None, since=since, mode="created")
                async for item in search_one_query(client, low_base_query, cancel_check):
                    ...去重 + yield + count 检查...

        # ----- 2.2 活跃榜 (pushed >= since): period 内有 push 的活跃仓库 -----
        # 查询字符串: "stars:>10 pushed:>=2026-07-29"
        base_active_query = build_candidate_query(threshold, language=None, since=since, mode="pushed")
        async for item in search_one_query(client, base_active_query, cancel_check):
            gid = item["id"]
            if gid 为 None 或 gid ∈ seen_ids:
                continue
            seen_ids.add(gid)
            yield item
            count += 1
            if count >= max_candidates:
                return

        # ----- 2.3 按语言迭代查询 (突破 Search API 单查询 1000 条上限) -----
        # GitHub Search API 单次查询最多返回 1000 条 (10页 × 100/页)
        # 通过按语言拆分, 等效于 N × 1000 条候选池
        # 每个 lang 同时查 created + pushed, 覆盖非主流语言的活跃老仓库
        for lang in languages:
            if lang 为 None:
                continue                  # 跳过 [None] 占位项
            # 取消检查点: 每种语言开始前
            if cancel_check 且 cancel_check() 返回 True:
                raise RuntimeError("用户手动停止抓取")

            # 查询字符串: "stars:>10 language:Python created:>=2026-07-29" (monthly 跳过)
            if not skip_created:
                lang_new_query = build_candidate_query(threshold, language=lang, since=since, mode="created")
                async for item in search_one_query(client, lang_new_query, cancel_check):
                    ...去重 + yield + count 检查...

            # 查询字符串: "stars:>10 language:Python pushed:>=2026-07-29" (新增)
            lang_push_query = build_candidate_query(threshold, language=lang, since=since, mode="pushed")
            async for item in search_one_query(client, lang_push_query, cancel_check):
                ...去重 + yield + count 检查...

    # 流式抓取结束
    log("Streaming crawl done: {count} unique candidates yielded")
```

### 3.2 关键子流程伪代码

#### `fetch_trending_page` — 抓取 Trending HTML

```
函数 fetch_trending_page(client, period, language):
    # 请求 github.com/trending?since=daily&language=python
    params = {"since": period}
    if language 非空:
        params["language"] = language

    resp = await client.get(
        "https://github.com/trending",
        params=params,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
        timeout=30s,
        follow_redirects=True,
    )
    if resp.status_code != 200:
        return []                         # Trending 页面不可用时降级, 仅依赖 Phase 2

    html = resp.text
    # 解析 HTML 中的 repo 列表 (article.Box-row 节点)
    items = 解析每个仓库节点:
        full_name     = owner/repo
        stars_gained  = 解析 "N stars today/this week/this month"  # 真实增量!
        total_stars   = 解析总 star 数
        language      = 解析语言标签
        description   = 解析描述
    return items                          # 通常 Top 25 个
```

#### `fetch_trending_repos_detail` — 补全 Trending 仓库详情

```
异步生成器 fetch_trending_repos_detail(client, trending_items, cancel_check):
    for item in trending_items:
        # 取消检查点
        if cancel_check 且 cancel_check() 返回 True:
            raise RuntimeError("用户手动停止抓取")

        full_name = item["full_name"]
        owner, repo = full_name.split("/", 1)
        page_gained = item.get("stars_gained", 0)     # Trending 页面的真实增量

        # 调用 /repos/{owner}/{repo} API 获取完整信息 (id, topics, license, created_at, ...)
        api_data = await get_repo_info(client, owner, repo)

        if api_data 且 "id" in api_data:
            # 注入真实增量, 后续 _compute_stars_gained 会优先使用
            if page_gained > 0:
                api_data["_stars_gained_from_page"] = page_gained
            yield api_data
        else:
            # API 失败时降级: 用 Trending 页面的基础信息构造最小 item
            yield 构造最小 item (id=None, full_name, stargazers_count=total_stars, ...)
```

#### `search_one_query` — Search API 分页拉取

```
异步生成器 search_one_query(client, query, cancel_check):
    # GitHub Search API 单查询最多返回 1000 条 (受 max_pages 限制)
    for page in 1 to self.max_pages:                  # 默认 10 页
        # 取消检查点: 每页请求前
        if cancel_check 且 cancel_check() 返回 True:
            raise RuntimeError("用户手动停止抓取")

        # 限流: Search API 30 req/min, 间隔 2 秒
        await _throttle_search()                      # 保证距上次请求 >= 2s

        params = {
            "q": query,                               # 例 "stars:>10 created:>=2026-07-29"
            "sort": "stars",                          # 按 star 数降序
            "order": "desc",
            "per_page": 100,                          # 每页最大 100
            "page": page,
        }
        data = await _request_with_retry(             # 带 3 次重试 + 退避
            client,
            "https://api.github.com/search/repositories",
            params,
            self._headers,
        )
        if data 为 None:
            break                                     # 彻底失败, 结束本查询

        items = data.get("items", [])
        if items 为空:
            break                                     # 没有更多结果

        for item in items:
            yield item                                # 流式吐出, 供外层去重

        # 提前终止: 已拉取数 >= total_count (GitHub 告知的总数)
        if total_fetched >= data["total_count"]:
            break
```

#### `build_candidate_query` — 构造查询字符串

```
函数 build_candidate_query(threshold, language, since, mode):
    q = f"stars:>{threshold}"                          # 例 "stars:>10"
    if language 非空:
        q += f" language:{language}"                   # 例 "language:Python"
    if since 非空:
        if mode == "created":
            q += f" created:>={since.isoformat()}"     # 新秀榜: period 内创建
        elif mode == "pushed":
            q += f" pushed:>={since.isoformat()}"      # 活跃榜: period 内有 push
    return q
```

### 3.3 关键设计要点

| 要点 | 说明 |
|------|------|
| **Phase 1 优先** | Trending HTML 提供 100% 真实榜单 + 真实 `stars_gained`，是数据质量最高的来源，但仅 Top 25 |
| **真实增量注入** | Trending 页面的 `stars_gained` 通过 `_stars_gained_from_page` 字段注入 item，后续计算优先使用，避免估算 |
| **Phase 2 补充** | 用 `created>=since` 和 `pushed>=since` 两类查询补充 Trending 之外的高增长仓库 |
| **按语言迭代** | 单查询受 1000 条上限，按 `DEFAULT_TOP_LANGUAGES` 拆分（默认 Top 20 语言），等效扩大候选池 |
| **全局去重** | `seen_ids` 集合跨 Phase 1/2 去重，同一仓库只 yield 一次 |
| **流式 yield** | 每收到一个 item 立即 yield，调用方逐条入库 + 计算快照，实现实时进度 |
| **提前终止** | 每次 yield 后检查 `count >= max_candidates`，达到 500 立即停止 |
| **取消响应** | 多个检查点：进入搜索前、每种语言开始前、每页请求前、每个 Trending 仓库处理前 |
| **限流** | Search API 间隔 2 秒（30 req/min），Core API 间隔 1 秒（60 req/min） |
| **重试** | `_request_with_retry` 最多 3 次重试，指数退避基数为 5 秒 |
| **降级** | Trending 页面不可用时返回空列表，仅依赖 Phase 2；单个 /repos API 失败时用基础信息构造最小 item |

---

## 4. `stars_gained` 计算逻辑（核心）

计算函数 [_compute_stars_gained](file:///d:/dingfeng/work/aiproject/find-github/backend/app/crawler/tasks.py#L283-L351) 采用**快照差值法**，不依赖 stargazers API（因此不受 fine-grained PAT 权限限制）。

### 4.1 优先级 1：Trending 页面真实增量

若候选来自 Phase 1 的 github.com/trending HTML 抓取，会携带 `_stars_gained_from_page` 字段（GitHub 官方榜单上的真实增量），优先使用该值，标记为非估算：

```python
page_gained = item.pop("_stars_gained_from_page", None)
if page_gained is not None and page_gained > 0:
    gained = page_gained
    is_estimated = False
```

### 4.2 优先级 2：快照差值法（有历史快照）

查询**同 period** 且 `snapshot_date <= today - period_days` 的历史快照（取最近一条），做差：

```
baseline_date = today - period_days          # daily: today-1, weekly: today-7, monthly: today-30
prev_snap = 查询 snapshot_date <= baseline_date 的最近一条
stars_gained = max(0, 当前总star − prev_snap.stars_at_snapshot)
is_estimated = False
```

- 只查同 period 的历史快照（daily 只跟 daily 比，weekly 只跟 weekly 比，monthly 只跟 monthly 比）。
- **关键**：必须查询 `<= today - period_days` 的快照，而非"最近一条"。因为 weekly/monthly 每天都写快照，"最近一条"就是昨天，差值仅为 1 天增量；查询 period_days 天前的快照才能得到该 period 真实的 7/30 天增量。
- **窗口校验**：若取到的 prev_snap 距 today 超过 `period_days + 2` 天（容忍 1-2 天抓取空缺），则不采用该差值，降级走估算逻辑并记录 warning。

### 4.3 优先级 3：首次运行估算（无历史快照）

首次运行、无同 period 历史快照时，按仓库年龄 + 活跃度估算：

**基础估算**：

```
base_estimated = current_stars / repo_age_days * period_days
```

即按仓库自创建以来的平均日增长率推算 period 内新增。

**活跃度因子**（基于 `pushed_at`）：

| 条件 | activity_factor |
|------|----------------|
| 今天有 push（`days_since_push <= 0`） | 1.3 |
| period 内有 push（`days_since_push <= period_days`） | 1.0 |
| period 外无 push | `max(0.1, period_days / (period_days + days_since_push))` |

**最终估算**：

```
estimated = int(base_estimated * activity_factor)
estimated = min(estimated, current_stars)   # 上限保护: 不超过总 star
is_estimated = True
```

活跃度因子的作用：让不同 period 的估算值不再线性放大（否则 monthly 总是 daily 的 30 倍），更贴近 trending 语义——短期内有 push 的仓库才是真正活跃的 trending，长期不活跃仓库在短期 period 中排名靠后。

---

## 5. 热度评分（Score）

计算函数 [_compute_score](file:///d:/dingfeng/work/aiproject/find-github/backend/app/crawler/tasks.py#L381-L419)：

```
Score = ΔStars × small_repo_penalty × (1 / log10(TotalStars + 10)) × age_factor
```

| 因子 | 作用 |
|------|------|
| `ΔStars` | period 内新增 star（主要排序依据） |
| `small_repo_penalty` | 低基数惩罚：`total_stars < 50` 时线性衰减（10★→0.2, 49★→0.98, ≥50★→1.0），抑制刷星/营销号灌水 |
| `1 / log10(TotalStars + 10)` | 对高 Star 总量项目惩罚，提升低基数黑马权重（100★→0.5, 1000★→0.33, 10000★→0.25） |
| `age_factor` | 时间衰减 `max(0.3, e^(-λ × AgeInDays))`，鼓励新项目（λ=0.001，1年→0.69, 3年→0.34, 5年→0.3, 10年→0.3）。下限 0.3 防止过度压制成熟基础设施项目 |

**排名依据**：trending 榜单按 `score` 降序排名（而非 `stars_gained`），防止老牌大项目霸榜。仅 `stars_gained > threshold` 的候选才进入 trending 排名。

---

## 6. 历史高分池兜底（Phase 3）

函数 [_refresh_historical_pool](file:///d:/dingfeng/work/aiproject/find-github/backend/app/crawler/tasks.py#L428)：

- **场景**：老仓库近期突然爆发（"老树开花"），但不在 created/pushed 的前 1000 条搜索结果里。
- **逻辑**：查询近 `HISTORICAL_POOL_DAYS=7` 天有快照、但本次未抓取的仓库，按历史 `score` 降序取 Top `HISTORICAL_POOL_TOP_N=200`（score 综合了增量+基数+年龄，比纯 stars_gained 更稳定）。
- **动作**：对这些仓库调用 `/repos/{owner}/{repo}` 获取最新 star 数，重新计算 `stars_gained` + `score` 并写快照。
- **目的**：控制 API 消耗的同时，兜底爆发式增长的老仓库。

---

## 7. Snapshot 数据模型

定义在 [models.py](file:///d:/dingfeng/work/aiproject/find-github/backend/app/models.py#L80-L110)：

| 字段 | 说明 |
|------|------|
| `repository_id` | 关联仓库 |
| `snapshot_date` | 快照日期（东八区） |
| `period` | `daily` / `weekly` / `monthly` |
| `stars_at_snapshot` | 快照时刻的总 star |
| `forks_at_snapshot` | 快照时刻的总 forks |
| `stars_gained` | 该 period 内的新增 star |
| `score` | 热度评分 |
| `rank_in_period` | 该 period 内的 trending 排名 |
| `crawl_run_id` | 关联抓取记录 |

**唯一约束**：`(repository_id, period, snapshot_date)`——同一仓库同一 period 同一天只有一条快照，重复抓取会更新而非新增。

每个仓库每个 period 每天最多一条快照。三个 period 独立存储，互不干扰。

---

## 8. 前端 `period=all` 聚合去重逻辑

当用户在前端选择 `period=all`（"全部"）时，后端 [repos.py](file:///d:/dingfeng/work/aiproject/find-github/backend/app/api/repos.py#L191-L195) 的行为：

```python
elif period == "all":
    # period=all: 只返回在任意 period (daily/weekly/monthly) 有快照的仓库 (聚合去重)
    cond = Snapshot.repository_id == Repository.id
    query = query.filter(exists().where(cond))
```

**聚合去重规则**：
- 只要仓库在 daily / weekly / monthly **任意一个** period 有快照，就会出现在列表中。
- 通过 `exists` 子查询去重，同一仓库只出现一次（不会因为三个 period 都有快照而出现三遍）。
- `period=all` 时**隐藏"新增★"列**（见项目硬约束），因为不同 period 的 `stars_gained` 不可直接比较，`_attach_stars_gained` 在 `period=all` 时返回空映射。
- 排序按常规字段（stars / forks / updated 等），`sort=gained` 在 `period=all` 时降级为 `stars` 排序。

---

## 9. 前端按 period 排序与展示

当指定具体 period（daily / weekly / monthly）时：

- **`_attach_stars_gained`**：批量查询每个 repo 在该 period **最新一天**的 `stars_gained`，通过子查询找每个 repo 的 `max(snapshot_date)`，再 join 取对应快照的 `stars_gained`。
- **`sort=gained`**：走特殊路径，Repository join 最新快照，按 `Snapshot.stars_gained` 排序（而非 Repository 表字段）。
- **聚合统计**（`_compute_aggregates`）：跨所有分页计算 `total_gained`（仅 period != all 时有意义）、`total_stars`、`total_forks`、`chinese_count`、`interpreted_count`。

---

## 10. 完整数据流示例

假设今天是 `2026-07-30`，系统已运行多日：

### daily 抓取（00:00 执行）
1. `since = 2026-07-29`
2. 抓取 created/pushed >= 2026-07-29 的候选仓库
3. 对每个候选：查 `period='daily'` 且 `snapshot_date <= 2026-07-29` 的最近一条历史快照（即 2026-07-29 的 daily 快照）
4. `stars_gained = 今天总star − 昨天总star`
5. 写今天的 daily 快照

### daily 刷新（12:00 执行）
1. 抓取 created/pushed >= 2026-07-29 的候选仓库
2. 仅 upsert Repository 表（更新最新 star/forks/updated_at），**不写 Snapshot**
3. 不计算 stars_gained、不回填排名、不重建缓存

### weekly 抓取（02:00 执行）
1. `since = 2026-07-23`
2. 抓取 created/pushed >= 2026-07-23 的候选仓库
3. 对每个候选：查 `period='weekly'` 且 `snapshot_date <= 2026-07-23` 的最近一条历史快照（即 7 天前的 weekly 快照）
4. `stars_gained = 今天总star − 7天前快照总star`（真实 7 天增量）
5. 写今天的 weekly 快照

### monthly 抓取（04:00 执行）
1. `since = 2026-06-30`
2. 抓取 created/pushed >= 2026-06-30 的候选仓库
3. 对每个候选：查 `period='monthly'` 且 `snapshot_date <= 2026-06-30` 的最近一条历史快照（即 30 天前的 monthly 快照）
4. `stars_gained = 今天总star − 30天前快照总star`（真实 30 天增量）
5. 写今天的 monthly 快照

### 首次运行（无任何历史快照）
- 三个 period 都走估算逻辑（§4.3）
- 估算值带活跃度因子，且不超过总 star
- 第二天起，各 period 开始有历史快照，切换为精确差值

---

## 11. 关键约束总结

| 约束 | 说明 |
|------|------|
| Star 新增量必须用 period 内新增量 | 不能用总 star 数代替 |
| 快照差值法优先 | fine-grained PAT 无法访问 stargazers API，必须用快照差值 |
| 差值基准 = `today - period_days` | 查询 `snapshot_date <= today - period_days` 的快照，而非"最近一条"（否则 weekly/monthly 只算 1 天增量） |
| 差值窗口校验 | prev_snap 距 today 不得超过 `period_days + 2` 天，超出则降级估算 |
| 三个 period 独立存储 | daily / weekly / monthly 各自维护快照序列，互不干扰 |
| weekly/monthly 每天执行 | 保证差值基准存在，避免估算 |
| daily 12:00 不写快照 | 刷新模式 `write_snapshot=False`，避免覆盖 00:00 差值基准 |
| 候选上限 500 条 | `GITHUB_CRAWL_MAX_CANDIDATES=500` |
| 估算值不超过总 star | `min(estimated, current_stars)` |
| trending 按 score 排名 | 防止老牌大项目霸榜 |
| `period=all` 隐藏"新增★"列 | 不同 period 不可直接比较 |
| `period=all` 聚合去重 | 任意 period 有快照即出现，同仓库只出现一次 |
