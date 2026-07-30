# find-github
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![React: 18](https://img.shields.io/badge/React-18-61dafb.svg)](https://react.dev/)
[![Database: SQLite](https://img.shields.io/badge/Database-SQLite-003B57.svg)](https://www.sqlite.org/)

> 中国开发者的技术雷达 · 数据基于 GitHub Search API + AI 中文解读

find-github 是一个定时(每日 / 每周 / 每月)从 GitHub 发现热门仓库的全栈应用,
入库存储后提供可多维度筛选的前端展示页面。通过 GitHub Search API 配合分语言
迭代查询突破 1000 条上限,同时抓取 GitHub Trending 页面确保核心仓库 100% 匹配,
并用 LLM 生成中文解读,帮助中文开发者快速发现值得关注的开源项目。

---

## ✨ 特性

- **完整榜单** — GitHub Search API 配合分语言迭代查询突破单查询 1000 条上限,同时抓取
  GitHub Trending 页面确保核心仓库 100% 匹配; 候选仓库上限 500 条 (`GITHUB_CRAWL_MAX_CANDIDATES`)。
- **三档时段** — daily / weekly / monthly 各自独立抓取与快照; "全部"时段聚合
  三档去重列表。
- **stars_gained 计算** — 优先使用快照差值法 (当前总 star − `today - period_days` 那天的同 period 历史快照 star);
  首次运行无快照时按仓库年龄 + pushed_at 活跃度因子估算, 上限保护 (不超过总 star)。
  不依赖 stargazers API, 不受 fine-grained PAT 权限限制。
- **热度评分排名** — Trending 榜单按 `Score = ΔStars × small_repo_penalty × (1/log10(TotalStars+10)) × age_factor`
  排序, 对高基数老牌项目做对数惩罚 + 时间衰减 (下限 0.3), 低基数仓库 (<50★) 线性惩罚抑制刷星, 防止霸榜。
- **历史高分池** — 兜底"老树开花": 刷新近 7 天有快照但本次未抓取的仓库 (Top 200, 按历史 score 降序),
  重新拉取最新数据计算 stars_gained, 避免遗漏短期爆发的老仓库。
- **历史快照** — 每次抓取写入 snapshots 表,用于绘制 star 趋势曲线。
- **多维筛选** — 时段、语言、分类、地区、Star 范围、主题、许可证、关键词、行业等;
  所有下拉均含"全部"选项。
- **国产开源识别** — 通过 GitHub User API 获取 owner location/company/bio,
  匹配中国关键词。
- **技术分类** — 基于 topics/description/language 关键词规则匹配,覆盖 11 个领域
  (ai/frontend/backend/devops/security/database/mobile/game/data/blockchain/iot)。
- **效率工具识别** — 通过特定 topics + 描述关键词,按行业分类
  (developer/pm/office/ai-assistant/writing/operation)。
- **AI 中文解读** — 兼容 OpenAI 格式 LLM (DeepSeek/Qwen/OpenAI/Ollama),生成
  一句话简介 + 价值解读 + 难度 + 学习时长 + 适合人群 + 替代品;支持单仓库同步生成
  和全量并行解读,实时进度反馈。定时抓取成功后会自动对未解读仓库补跑解读。
- **README 中文化** — 仓库详情页可拉取原始 README (按优先级匹配 README.zh-CN.md 等
  中文文件名, 内存缓存 5 分钟), 并支持一键 AI 翻译为中文 (保持 Markdown 格式, 翻译
  结果内存缓存 7 天)。
- **管理员鉴权** — 敏感操作 (触发抓取 / 停止任务 / 全量 AI 解读) 需密码验证, 验证
  通过后签发 HMAC 签名 token (无状态, 默认 12 小时有效); 密码推荐以 PBKDF2 哈希
  存储 (`python -m cli hashpw` 生成), 鉴权接口按 IP 限流防暴力破解, 未配置凭证时
  敏感接口返回 503 拒绝访问。
- **东八区时区** — 系统前后端统一使用 Asia/Shanghai 时区。
- **混合调度** — APScheduler 内置自动运行 + 手动触发 + CLI。
- **单文件部署** — SQLite 单文件存储,FastAPI 静态托管前端构建产物。

---

## 🛠️ 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.10+ / FastAPI / SQLAlchemy 2.x / APScheduler / httpx |
| 数据库 | SQLite (单文件, 零配置) |
| 前端 | React 18 + TypeScript + Vite + Ant Design 5 + Recharts |
| LLM | 兼容 OpenAI 格式 (DeepSeek / Qwen / OpenAI / Ollama / 自定义中转) |

---

## 🚀 快速开始

### 1. 后端安装

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 配置 GITHUB_TOKEN / LLM_API_KEY / ADMIN_PASSWORD_HASH (运行 `python -m cli hashpw` 生成)
```

### 2. 配置项 (`backend/.env`)

| 变量 | 默认 | 说明 |
|------|------|------|
| `GITHUB_TOKEN` | (空) | GitHub Personal Access Token, 提高速率限制 |
| `GITHUB_CRAWL_THRESHOLD` | 10 | Star 阈值 |
| `GITHUB_CRAWL_TOP_LANGUAGES` | 20 | 按语言迭代查询数 (突破 1000 条上限), 设 0 关闭 |
| `GITHUB_CRAWL_MAX_PAGES_PER_QUERY` | 10 | 单查询最大页数 (per_page=100, 10 页 = 1000 条) |
| `GITHUB_CRAWL_MAX_CANDIDATES` | 500 | 候选仓库上限 |
| `DATABASE_URL` | sqlite:///./data/findgithub.db | 数据库 URL |
| `SCHEDULER_ENABLED` | true | 是否启用 APScheduler |
| `CORS_ORIGINS` | http://localhost:5173 | CORS 允许来源 (逗号分隔多个) |
| `LLM_API_BASE` | https://api.deepseek.com/v1 | LLM API 基址 (兼容 OpenAI 格式) |
| `LLM_API_KEY` | (空) | LLM API Key |
| `LLM_MODEL` | deepseek-chat | LLM 模型名 |
| `LLM_TIMEOUT` | 60 | LLM 超时 (秒) |
| `LLM_CONCURRENCY` | 5 | AI 解读并发数 |
| `AI_INTERPRETATION_ENABLED` | true | 是否启用 AI 解读 (无 key 时设 false 跳过) |
| `ADMIN_PASSWORD_HASH` | (空) | 管理员密码哈希 (PBKDF2, 推荐, `python -m cli hashpw` 生成) |
| `ADMIN_PASSWORD` | (空) | 管理员密码明文 (向后兼容, 启动时 warning, 不推荐) |
| `SESSION_SECRET` | (随机) | 签名 token 密钥 (HMAC), 留空启动时随机生成, 重启后已签发 token 失效 |
| `SESSION_TTL_HOURS` | 12 | 管理员 token 有效期 (小时) |

> 两者均未配置时, 敏感接口 (触发抓取/停止/全量解读) 返回 503 拒绝访问。

### 3. 建表 + 启动后端

```bash
cd backend
python -m cli initdb
python -m cli server
# 访问 http://127.0.0.1:8000
# API 文档 http://127.0.0.1:8000/docs
```

### 4. 前端开发模式 (可选)

```bash
cd frontend
npm install
npm run dev
# 访问 http://localhost:5173 (自动代理 /api 到后端 8000)
```

### 5. 生产部署 (单进程)

```bash
cd frontend && npm run build
cd ../backend && python -m cli server
# 访问 http://127.0.0.1:8000 (FastAPI 静态托管前端 dist)
```

---

## 📁 目录结构

```
find-github/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 应用入口 (CORS / 路由注册 / SPA 静态托管)
│   │   ├── config.py            # 配置 (pydantic-settings 读取 .env)
│   │   ├── database.py          # SQLAlchemy engine / session / init_db
│   │   ├── models.py            # ORM: Repository/Snapshot/CrawlRun/AIInterpretation/TrendingCache
│   │   ├── schemas.py           # Pydantic 响应模型
│   │   ├── security.py          # 管理员鉴权 (PBKDF2 哈希 / HMAC 签名 token / IP 限流)
│   │   ├── scheduler.py         # APScheduler 调度 (抓取成功后自动补跑 AI 解读)
│   │   ├── tz.py                # 东八区时区工具 (now_cn / to_cn_datetime)
│   │   ├── api/
│   │   │   ├── auth.py          # /api/auth           管理员密码验证 + 签发 token
│   │   │   ├── repos.py         # /api/repos          仓库列表+详情+快照+README+翻译
│   │   │   ├── stats.py         # /api/stats          统计聚合 (汇总/语言/Top/时间线/分类/雷达/行业)
│   │   │   ├── runs.py          # /api/runs           抓取记录+手动触发+停止
│   │   │   ├── interpret.py     # /api/interpret      AI解读触发+进度查询
│   │   │   └── trending.py      # /api/v1/trending    Trending榜单 (优先读缓存)
│   │   └── crawler/
│   │       ├── github.py        # GitHub Search API + Trending 页面客户端
│   │       ├── tasks.py         # 抓取任务编排 (流式入库/快照差值/评分排名/缓存重建/可取消)
│   │       ├── interpreter.py   # AI 中文解读 (并行调用 LLM)
│   │       ├── interpret_progress.py  # 全量解读进度跟踪 (进程内全局状态)
│   │       └── classifier.py    # 技术分类/效率工具识别/中文文档检测/地区检测
│   ├── cli.py                   # CLI 入口 (initdb/crawl/interpret/reclassify/archive-snapshots/hashpw/server)
│   ├── requirements.txt
│   ├── .env.example             # 配置模板
│   └── .env                     # 实际配置 (含 token, 不提交)
├── frontend/                    # React + Vite 前端
│   ├── src/
│   │   ├── main.tsx             # React 入口
│   │   ├── App.tsx              # 路由 + 布局 (导航/时钟/星空背景)
│   │   ├── index.css            # 全局样式
│   │   ├── pages/
│   │   │   ├── DashboardPage.tsx  # 仪表盘 (统计+语言Top15+时间线+Top10)
│   │   │   ├── ReposPage.tsx      # 仓库列表 (多维筛选+排序+收藏, 复用于国产开源页)
│   │   │   ├── RepoDetailPage.tsx # 仓库详情 (快照趋势+AI解读+README中文化)
│   │   │   ├── EfficiencyPage.tsx # 效率工具专区 (行业分类)
│   │   │   ├── RadarPage.tsx      # 技术雷达
│   │   │   └── RunsPage.tsx       # 抓取记录 (管理员验证+全量AI解读+进度)
│   │   ├── components/            # GalaxyBackground (星空) / Sparkline (迷你趋势)
│   │   ├── hooks/                 # useDebouncedInput / useInterpAuthed / useScatterReveal
│   │   ├── utils/                 # index (收藏 localStorage 等) / period (时段工具)
│   │   ├── api/client.ts        # axios 封装 (所有后端接口调用)
│   │   └── types/index.ts       # TypeScript 类型
│   ├── public/                  # favicon 等静态资源
│   ├── package.json
│   └── vite.config.ts
└── data/                        # SQLite 数据库文件 (自动创建, 不提交)
```

---

## ⌨️ CLI 命令

```bash
cd backend
python -m cli initdb                                    # 建表
python -m cli crawl --period daily   [--threshold 10]   # 手动抓取 (threshold 默认读 settings)
python -m cli crawl --period weekly  [--threshold 10]
python -m cli crawl --period monthly [--threshold 10]
python -m cli interpret [--limit 50] [--force]          # 生成 AI 解读
python -m cli reclassify                                # 用最新规则重新分类所有仓库
python -m cli hashpw [--password <pwd>]                 # 生成管理员密码哈希 (写入 ADMIN_PASSWORD_HASH)
python -m cli server [--host 0.0.0.0] [--port 8000] [--reload]  # 启动 API + 调度器
python -m cli -v crawl --period daily                   # 详细 (DEBUG) 日志
```

---

## 🌐 API 端点

### 仓库
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/repos` | 仓库列表 (分页 + 多维筛选 + period 聚合去重 + 跨页收藏过滤) |
| GET | `/api/repos/{id}` | 仓库详情 (含全部快照和 AI 解读) |
| GET | `/api/repos/{id}/snapshots` | 仓库所有快照 |
| GET | `/api/repos/{id}/interpretation` | 仓库 AI 解读列表 |
| GET | `/api/repos/{id}/readme` | 仓库 README (内存缓存 5 分钟, 优先匹配中文 README) |
| POST | `/api/repos/{id}/translate-readme` | AI 翻译 README 为中文 (保持 Markdown, 翻译缓存 7 天) |

### 统计
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/stats/summary` | 仪表盘汇总 (含国产/效率工具/已解读数) |
| GET | `/api/stats/languages` | 语言分布 (可按 period 过滤) |
| GET | `/api/stats/top` | Top N 仓库 (指定 period 时按 stars_gained 排序) |
| GET | `/api/stats/timeline` | 每日抓取量时间线 |
| GET | `/api/stats/categories` | 技术分类统计 |
| GET | `/api/stats/radar` | 技术雷达 (每类返回 Top 仓库) |
| GET | `/api/stats/industries` | 效率工具行业统计 |

### 抓取记录 (管理员验证)
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/runs` | 抓取记录列表 (可按 period/status 筛选) |
| GET | `/api/runs/{id}` | 获取单条抓取记录 |
| POST | `/api/runs/trigger` | 手动触发抓取 (异步, 同 period 互斥) |
| POST | `/api/runs/{id}/cancel` | 停止运行中任务 (僵尸记录直接标记 cancelled) |

### AI 解读 (管理员验证)
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/interpret` | 触发 AI 解读 (后台线程, 可指定 repo_ids/force) |
| POST | `/api/interpret/sync` | 同步 AI 解读 (阻塞, 小批量) |
| POST | `/api/interpret/all` | 全量 AI 解读 (仅未解读仓库, 后台并行) |
| GET | `/api/interpret/progress` | 查询全量解读进度 (无需鉴权) |

### 其他
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/verify` | 验证管理员密码, 签发 HMAC token (IP 限流) |
| GET | `/api/v1/trending` | Trending 榜单 (language=all 优先读缓存, 其他实时计算) |
| GET | `/api/health` | 健康检查 |
| GET | `/docs` | FastAPI 自动生成的 OpenAPI 文档 |

---

## 🖥️ 前端页面

| 路由 | 页面 | 说明 |
|------|------|------|
| `/` | 仪表盘 | 统计卡片 + 语言分布 Top 15 + 近 30 天抓取时间线 + Top 10 仓库 |
| `/repos` | 仓库列表 | 多维筛选 + 排序 + 收藏 (localStorage); 全部/每日/每周/每月切换 |
| `/chinese` | 国产开源 | 复用 ReposPage (region=china), 默认按新增 star 排序 |
| `/efficiency` | 效率工具专区 | 按行业分类的效率工具 |
| `/radar` | 技术雷达 | 各分类 Top 仓库 |
| `/repos/:id` | 仓库详情 | 元信息 + Star 趋势 + AI 解读 + README 中文化 |
| `/runs` | 抓取记录 | 管理员验证后可触发抓取/停止/全量AI解读, 实时进度 |

---

## 🗄️ 数据模型

### `repositories` 表
仓库主表, 按 `github_id` 唯一. 包含基础信息 + 产品扩展字段
(region/is_chinese_owner/has_chinese_doc/category/is_efficiency_tool/industry).

### `snapshots` 表
每次抓取的快照, (repository_id, period, snapshot_date) 唯一.
包含 stars_at_snapshot/forks_at_snapshot/stars_gained/score/rank_in_period.

### `crawl_runs` 表
抓取运行记录, 包含状态 (running/success/failed/cancelled)/起止时间/发现数/入库数/错误信息/参数.

### `ai_interpretations` 表
AI 中文解读, 每个仓库保留最新一条.
包含 summary_cn/value_prop/difficulty/learning_hours/suitable_for/alternatives/model.

### `trending_cache` 表
Trending 榜单缓存, 按 (time_window, language, rank) 唯一, 预计算 Top N 写入
(`language=all` + 热门语言各一份), 供 `/api/v1/trending` 高性能读取. 包含 rank/delta_stars/score/calculated_at.

### `snapshot_monthly_summary` 表
快照月度摘要, 归档任务将超期 (默认 90 天) 明细快照按 (repository_id, period, year, month) 聚合后写入,
保留月末总 star / 月内最大 stars_gained / 最大 score / 快照条数, 控制 snapshots 表大小.

---

## ⏰ 调度方案

### 方案 A: 应用内置调度 (默认, 推荐)

`SCHEDULER_ENABLED=true` 时自动注册 (时区 Asia/Shanghai):

| 任务 | 执行时间 | 说明 |
|------|----------|------|
| daily | 每天 00:00 | 完整抓取, 写快照 (建立差值基准) |
| daily_refresh | 每天 12:00 | 仅刷新 Repository 表, 不写快照 (避免覆盖 00:00 基准) |
| weekly | 每天 02:00 | 每天写快照, stars_gained = 今天 − 7天前快照 |
| monthly | 每天 04:00 | 每天写快照, stars_gained = 今天 − 30天前快照 |
| archive_snapshots | 每天 03:00 | 将超期明细快照聚合成月度摘要后删除 (默认保留 90 天) |

> 三个时段每天都会执行, 确保 stars_gained 使用精确快照差值 (查询 `today - period_days` 那天的快照) 而非估算, 数据更新及时.
> daily 12:00 为刷新模式, 仅更新仓库最新计数, 不写快照避免基准漂移.
> 每次完整抓取成功后, 会自动对未解读仓库补跑 AI 解读 (force=False, 进度可在抓取记录页查看).

### 方案 B: 系统级 crontab

`SCHEDULER_ENABLED=false`, 配置 crontab 调用 CLI.

---

## ❓ 常见问题

### Q: stars_gained 是如何计算的?
- 有合格历史快照: 查询 `snapshot_date <= today - period_days` 的同 period 历史快照,
  `stars_gained = 当前总star − 历史快照star` (精确差值, 真实 N 天增量);
  若历史快照窗口超出 `period_days + 2` 天则降级估算.
- 首次运行无快照: 按仓库年龄估算 `total_stars / repo_age_days * period_days`,
  引入 pushed_at 活跃度因子, 上限不超过总 star

### Q: fine-grained PAT 无法访问 stargazers API?
fine-grained PAT (github_pat_ 开头) 无法访问 GraphQL/REST stargazers 端点.
系统使用快照差值法计算 stars_gained, 不依赖 stargazers API.

### Q: 如何清理历史数据?
删除 `data/findgithub.db` 文件, 重新 `python -m cli initdb`.

### Q: 全量 AI 解读进度怎么看?
在抓取记录页面点击「全量 AI 解读」后, 页面会实时显示进度条
(已处理/总数/百分比/成功率/当前仓库).

### Q: 管理员密码如何配置?
推荐: 运行 `python -m cli hashpw` (交互式输入, 隐藏回显), 将输出的
`ADMIN_PASSWORD_HASH=...` 写入 `backend/.env`. 也兼容 `ADMIN_PASSWORD` 明文配置
(启动时 warning, 不推荐). 两者均未配置时, 触发抓取/停止/全量解读等敏感接口返回 503.
验证通过后签发 HMAC token, 默认 12 小时有效 (由 `SESSION_TTL_HOURS` 控制).

### Q: 后端启动后没有反应?
- 检查端口 8000 是否被占用: `netstat -ano | findstr :8000` (Windows)
  或 `lsof -i:8000` (Linux/Mac)
- 确认已执行 `python -m cli initdb` 建表
- 查看控制台日志, 启动成功会输出 `Uvicorn running on http://0.0.0.0:8000`

---

## 🧪 开发说明

### 后端开发
```bash
cd backend
python -m cli server --reload  # 热重载开发模式
```

### 前端开发
```bash
cd frontend
npm run dev    # 开发服务器 (http://localhost:5173, 代理 /api 到 8000)
npm run build  # 生产构建到 dist/
```

### 代码规范
- 后端遵循 FastAPI + SQLAlchemy 2.x 规范, 配置通过 pydantic-settings 读取
- 前端使用 React 18 + TypeScript + Ant Design 5, 路由使用 react-router-dom v6
- 所有时区操作统一使用 `app.tz` 模块 (Asia/Shanghai)
- 鉴权模块 (`app.security`) 仅依赖 Python 标准库 (hashlib/hmac/secrets), 不引入 passlib/pyjwt 等第三方包

---

## 📝 License

MIT © 2026 dingfeng. See [LICENSE](LICENSE).
