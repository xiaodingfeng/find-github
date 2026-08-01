"""仓库分类与地区检测 - 基于规则的关键词匹配.

用于:
1. 技术领域分类 (frontend/backend/ai/devops/...)
2. 效率工具识别 + 行业分类 (developer/design/pm/writing/...)
3. 中文文档检测 (基于 topics/description)
4. owner 地区检测 (基于 GitHub user API 的 location)
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

# ===== 技术领域分类规则 (按优先级, 命中即停) =====
# 每条: (category, keywords_in_topics_or_desc, languages)
CATEGORY_RULES = [
    ("ai", {
        "topics": {"ai", "ai-agents", "llm", "machine-learning", "deep-learning", "nlp",
                   "transformer", "chatgpt", "langchain", "rag", "neural-network",
                   "computer-vision", "reinforcement-learning", "diffusion", "stable-diffusion",
                   "embedding", "vector-database", "prompt-engineering", "claude", "openai",
                   "anthropic", "gemini", "mcp", "model-context-protocol", "agent"},
        "desc_kw": ["ai agent", "llm", "大模型", "人工智能", "机器学习", "深度学习",
                    "chatgpt", "gpt", "transformer", "diffusion model", "rag",
                    "向量数据库", "embedding", "prompt", "智能体"],
        "languages": set(),
    }),
    ("frontend", {
        "topics": {"frontend", "react", "vue", "angular", "svelte", "solid", "nextjs",
                   "nuxt", "ui", "ui-components", "design-system", "css", "tailwind",
                   "component-library", "web-components", "webpack", "vite"},
        "desc_kw": ["前端", "ui 组件", "react", "vue", "组件库", "frontend"],
        "languages": {"JavaScript", "TypeScript", "HTML", "CSS", "Svelte"},
    }),
    ("backend", {
        "topics": {"backend", "api", "rest", "graphql", "microservices", "server",
                   "framework", "web-framework", "rpc", "grpc"},
        "desc_kw": ["后端", "api 框架", "backend", "web framework", "微服务"],
        "languages": {"Go", "Java", "Rust", "C++", "C", "Python", "Ruby", "PHP", "Scala", "Elixir"},
    }),
    ("devops", {
        "topics": {"devops", "kubernetes", "docker", "container", "ci-cd", "terraform",
                   "ansible", "helm", "observability", "monitoring", "infrastructure",
                   "cloud-native", "serverless", "k8s",
                   "argo", "argocd", "cilium", "istio", "prometheus", "grafana",
                   "vault", "envoy", "linkerd", "opentelemetry", "jaeger", "trivy",
                   "falco", "crossplane", "knative", "tekton", "k3s", "k0s",
                   "opa", "gatekeeper", "containerd", "podman", "buildah", "skaffold",
                   "helm-chart", "pulumi", "packer", "nomad", "consul"},
        "desc_kw": ["运维", "容器", "kubernetes", "docker", "devops", "基础设施",
                    "ci/cd", "监控", "云原生", "prometheus", "grafana", "istio",
                    "服务网格", "可观测性", "链路追踪", "argo", "容器编排"],
        "languages": set(),
    }),
    ("security", {
        "topics": {"security", "pentesting", "vulnerability", "cve", "exploit",
                   "cryptography", "encryption", "red-team", "blue-team", "siem",
                   "waf", "ids", "ips", "firewall", "reverse-engineering",
                   "malware", "antivirus", "yara", "suricata", "snort", "osquery",
                   "appsec", "devsecops", "zero-trust", "soar", "edr", "xdr",
                   "secret-scanning", "sast", "dast", "dependency-check"},
        "desc_kw": ["安全", "漏洞", "渗透", "加密", "security", "pentest", "crypto",
                    "waf", "防火墙", "入侵检测", "逆向", "恶意软件", "杀毒",
                    "应用安全", "devsecops", "零信任"],
        "languages": set(),
    }),
    ("database", {
        "topics": {"database", "sql", "nosql", "redis", "postgresql", "mysql",
                   "mongodb", "sqlite", "vector-database", "olap", "oltp",
                   "clickhouse", "duckdb", "tikv", "tidb", "cockroachdb", "supabase",
                   "surrealdb", "neon", "planetscale", "prisma", "drizzle-orm",
                   "sqlalchemy", "orm", "query-builder", "leveldb", "rocksdb",
                   "influxdb", "timescaledb", "cassandra", "elasticsearch",
                   "opensearch", "dynamodb", "etcd", "consul"},
        "desc_kw": ["数据库", "database", "sql", "向量数据库", "kv 存储",
                    "clickhouse", "duckdb", "tidb", "cockroachdb", "时序数据库",
                    "orm", "数据迁移", "连接池"],
        "languages": set(),
    }),
    ("mobile", {
        "topics": {"mobile", "android", "ios", "flutter", "react-native", "swift",
                   "kotlin", "mobile-app"},
        "desc_kw": ["移动", "android", "ios", "app 开发", "mobile"],
        "languages": {"Swift", "Kotlin", "Dart"},
    }),
    ("game", {
        "topics": {"game", "game-engine", "gamedev", "unity", "unreal", "godot"},
        "desc_kw": ["游戏", "game engine", "游戏开发"],
        "languages": set(),
    }),
    ("data", {
        "topics": {"data-engineering", "data-science", "etl", "data-pipeline",
                   "pandas", "spark", "hadoop", "airflow", "jupyter",
                   "dbt", "dagster", "prefect", "polars", "dvc",
                   "great-expectations", "delta-lake", "iceberg", "hudi",
                   "data-quality", "data-lineage", "feature-store", "jupyterlab",
                   "notebook", "dataset", "data-warehouse", "data-lake", "lakehouse",
                   "streaming", "kafka", "flink", "beam"},
        "desc_kw": ["数据工程", "data science", "etl", "数据分析", "数据管道",
                    "dbt", "dagster", "数据仓库", "数据湖", "特征工程",
                    "数据血缘", "数据质量", "流处理"],
        "languages": set(),
    }),
    ("blockchain", {
        "topics": {"blockchain", "web3", "solidity", "ethereum", "bitcoin",
                   "smart-contract", "defi", "nft", "crypto"},
        "desc_kw": ["区块链", "web3", "智能合约", "blockchain"],
        "languages": set(),
    }),
    ("iot", {
        "topics": {"iot", "embedded", "arduino", "raspberry-pi", "esp32", "firmware"},
        "desc_kw": ["物联网", "iot", "嵌入式", "embedded"],
        "languages": set(),
    }),
]

# ===== 效率工具识别 (跨行业, 宽口径) =====
# 任何能帮人提效的工具: CLI / 桌面应用 / 自动化 / 生产力 / 开发者工具 / 办公 / AI 助手 等
EFFICIENCY_TOPICS = {
    # 通用工具类 topics
    "cli", "command-line", "terminal", "shell", "console",
    "productivity", "automation", "workflow", "tools", "tool",
    "utility", "utilities", "boilerplate", "scaffolding", "shortcut",
    "task-runner", "build-tool", "developer-tools", "devtools",
    "self-hosted", "desktop", "desktop-app", "cross-platform",
    "electron", "tauri", "wails", "flutter", "qt",
    "free", "open-source", "awesome-list",
    # 开发者工具
    "ide", "editor", "linting", "debugging", "git", "version-control",
    "code-generator", "generator", "scaffolding", "snippet",
    "testing", "test-framework", "mock", "benchmark",
    "linter", "formatter", "language-server", "lsp",
    "package-manager", "dependency-management",
    # 生产力 / 办公
    "note-taking", "notes", "markdown", "documentation", "docs",
    "kanban", "todo", "to-do", "task-management", "project-management",
    "calendar", "reminder", "pomodoro",
    "office", "document", "pdf", "word", "excel", "ppt", "ocr",
    # AI 助手
    "ai-assistant", "chatbot", "copilot", "assistant", "agent",
    "ai-tools", "prompt", "llm-tools", "prompt-engineering",
    # 设计 / 媒体
    "design", "design-tools", "figma", "sketch", "ui-design",
    "prototype", "design-system",
    "video", "audio", "image", "ffmpeg", "multimedia", "streaming", "subtitle",
    # 数据 / 分析
    "data-visualization", "analytics", "dashboard", "bi",
    "spreadsheet", "report",
    # 运营 / 自动化
    "integration", "zapier", "no-code", "low-code",
    # 教育
    "education", "learning", "tutorial", "course", "flashcard",
    # 营销 / 财务
    "marketing", "seo", "social-media", "newsletter",
    "finance", "accounting", "invoice", "bookkeeping", "trading",
}

# description / name 中的效率工具信号词 (宽口径)
EFFICIENCY_DESC_KEYWORDS = [
    # 中文
    "工具", "工具箱", "效率", "助手", "自动化", "命令行", "终端", "脚手架",
    "管理", "管理器", "快速", "加速", "简化", "便捷", "方便", "提升",
    "办公", "文档", "笔记", "待办", "看板", "日历", "提醒",
    "编辑器", "ide", "插件", "扩展", "生成器", "构建器", "打包器",
    "可视化", "图表", "报表", "监控", "仪表盘",
    "聊天", "对话", "问答",
    # 英文 (词组)
    "tool", "tools", "toolkit", "toolbox",
    "cli ", "command-line", "command line", "terminal", "shell",
    "productivity", "automation", "automate", "workflow",
    "assistant", "helper", "wizard", "booster", "accelerator",
    "manager", "organizer", "planner", "tracker",
    "generator", "builder", "maker", "scaffolding", "boilerplate",
    "editor", "ide ", "plugin", "extension", "addon",
    "note", "todo", "kanban", "task", "project management",
    "office", "document", "pdf", "ocr",
    "dashboard", "visualization", "analytics",
    "chatbot", "copilot", "ai assistant", "ai tool",
    "design tool", "prototype",
    "no-code", "low-code",
    "self-hosted",
]

# 效率工具 - 行业分类规则 (按优先级, 命中即停)
INDUSTRY_RULES = [
    ("ai-assistant", {
        "topics": {"ai-assistant", "chatbot", "copilot", "assistant", "agent",
                   "ai-tools", "prompt", "llm-tools", "prompt-engineering",
                   "chatgpt", "openai", "claude", "anthropic", "gemini"},
        "desc_kw": ["ai 助手", "ai assistant", "copilot", "智能助手",
                    "chatbot", "聊天机器人", "ai 工具", "ai chat",
                    "对话", "问答", "llm", "大模型"],
    }),
    ("developer", {
        "topics": {"cli", "command-line", "terminal", "shell", "developer-tools",
                   "devtools", "ide", "editor", "linting", "debugging",
                   "boilerplate", "scaffolding", "build-tool", "task-runner",
                   "git", "version-control", "code-generator", "generator",
                   "snippet", "testing", "test-framework", "mock", "benchmark",
                   "linter", "formatter", "language-server", "lsp",
                   "package-manager", "dependency-management",
                   "developer-tools", "programming", "coding"},
        "desc_kw": ["开发者工具", "cli", "命令行", "终端", "shell",
                    "ide", "编辑器", "代码生成", "developer tool",
                    "command line", "terminal", "linter", "formatter",
                    "language server", "代码补全", "调试", "测试",
                    "git", "版本控制", "boilerplate", "脚手架",
                    "snippet", "代码片段", "package manager"],
    }),
    ("office", {
        "topics": {"office", "document", "pdf", "word", "excel", "ppt",
                   "ocr", "form", "docx", "xlsx", "pptx"},
        "desc_kw": ["办公", "office", "文档处理", "pdf", "ocr", "表单",
                    "word", "excel", "ppt", "演示文稿", "电子表格"],
    }),
    ("writing", {
        "topics": {"writing", "markdown", "note-taking", "notes",
                   "documentation", "docs", "blog", "obsidian", "notion",
                   "knowledge-management", "wiki"},
        "desc_kw": ["写作", "笔记", "文档", "writing", "note", "markdown 编辑器",
                    "知识管理", "obsidian", "notion", "博客", "wiki"],
    }),
    ("pm", {
        "topics": {"project-management", "kanban", "scrum", "agile", "jira",
                   "task-management", "todo", "to-do", "productivity",
                   "calendar", "reminder", "pomodoro"},
        "desc_kw": ["项目管理", "project management", "看板", "任务管理",
                    "todo", "待办", "productivity", "日程", "日历",
                    "提醒", "番茄钟", "agile", "scrum"],
    }),
    ("data", {
        "topics": {"data-visualization", "analytics", "dashboard", "bi",
                   "spreadsheet", "report", "chart", "visualization"},
        "desc_kw": ["数据可视化", "数据分析", "dashboard", "报表",
                    "excel", "商业智能", "bi", "图表", "可视化"],
    }),
    ("design", {
        "topics": {"design", "design-tools", "figma", "sketch", "ui-design",
                   "prototype", "design-system", "graphic-design"},
        "desc_kw": ["设计", "design tool", "figma", "原型", "ui 设计",
                    "平面设计", "图形设计", "sketch"],
    }),
    ("media", {
        "topics": {"video", "audio", "image", "ffmpeg", "multimedia",
                   "streaming", "subtitle", "recorder", "screen-recorder",
                   "editor", "converter"},
        "desc_kw": ["视频", "音频", "图片", "媒体", "video", "audio",
                    "ffmpeg", "字幕", "剪辑", "录屏", "录制", "格式转换"],
    }),
    ("operation", {
        "topics": {"automation", "workflow", "integration", "zapier",
                   "no-code", "low-code", "ifttt", "n8n"},
        "desc_kw": ["运营", "自动化", "workflow", "集成", "无代码", "低代码",
                    "no-code", "low-code", "zapier", "n8n", "工作流"],
    }),
    ("marketing", {
        "topics": {"marketing", "seo", "social-media", "content",
                   "email", "newsletter", "crm"},
        "desc_kw": ["营销", "marketing", "seo", "社媒", "内容运营",
                    "邮件营销", "newsletter", "客户管理", "crm"],
    }),
    ("finance", {
        "topics": {"finance", "accounting", "invoice", "bookkeeping",
                   "trading", "stock", "investment", "budget"},
        "desc_kw": ["财务", "会计", "finance", "记账", "发票", "股票",
                    "trading", "投资", "预算"],
    }),
    ("education", {
        "topics": {"education", "learning", "tutorial", "course",
                   "study", "flashcard", "spaced-repetition", "mooc"},
        "desc_kw": ["教育", "学习", "education", "learning", "教程",
                    "课程", "背单词", "记忆", "mooc"],
    }),
]

# ===== 行业 → 技术领域 反推映射 (classify_category 规则未命中时的 fallback) =====
# 当 topics/desc/language 全未命中 CATEGORY_RULES, 但 detect_efficiency_tool 识别出
# industry 时, 按行业反推一个合理的 category, 降低未分类率 (400 个 → 目标 <5%).
INDUSTRY_TO_CATEGORY_FALLBACK = {
    "ai-assistant": "ai",      # AI 助手 → ai
    "data": "data",            # 数据分析 → data
    "design": "frontend",      # 设计工具 → frontend
    "media": "frontend",       # 媒体处理 → frontend (多为前端展示/编辑)
    "developer": "backend",    # 开发者工具 → backend (GitHub 上多为后端/CLI)
    "office": "backend",       # 办公工具 → backend
    "writing": "frontend",     # 写作工具 → frontend (笔记/编辑器)
    "pm": "backend",           # 项目管理 → backend
    "operation": "backend",    # 运营自动化 → backend
    "marketing": "backend",    # 营销工具 → backend
    "finance": "backend",      # 财务工具 → backend
    "education": "backend",    # 教育工具 → backend
}


# ===== 中文文档检测关键词 =====
CHINESE_KEYWORDS = [
    "中文", "文档", "教程", "简介", "安装", "使用", "说明", "快速开始",
    "项目", "功能", "支持", "配置", "示例", "注意", "警告", "这是一个",
    "中文版", "中文文档", "简体中文",
]

# 中文字符范围检测 (CJK 统一汉字)
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def classify_category(item: Dict[str, Any]) -> Optional[str]:
    """根据 topics/description/language 识别技术领域分类."""
    topics = set(t.lower() for t in (item.get("topics") or []))
    desc = (item.get("description") or "").lower()
    language = item.get("language") or ""

    for category, rule in CATEGORY_RULES:
        # topics 命中
        if topics & rule["topics"]:
            return category
        # description 关键词命中
        for kw in rule["desc_kw"]:
            if kw in desc:
                return category
        # 语言命中
        if language in rule["languages"]:
            return category

    return None


def detect_efficiency_tool(item: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """识别是否为效率工具, 并返回行业分类.

    宽口径: 任何能帮人提效的工具都识别为效率工具.
    Returns:
        (is_efficiency, industry)
    """
    topics = set(t.lower() for t in (item.get("topics") or []))
    desc = (item.get("description") or "").lower()
    name = (item.get("name") or "").lower()
    full_name = (item.get("full_name") or "").lower()

    # 效率工具信号 - topics 命中
    is_eff = bool(topics & EFFICIENCY_TOPICS)

    # 效率工具信号 - description / name 关键词命中
    if not is_eff:
        text = f"{desc} {name}"
        for kw in EFFICIENCY_DESC_KEYWORDS:
            if kw in text:
                is_eff = True
                break

    if not is_eff:
        return False, None

    # 行业分类 (按优先级匹配)
    for industry, rule in INDUSTRY_RULES:
        if topics & rule["topics"]:
            return True, industry
        for kw in rule["desc_kw"]:
            if kw in desc or kw in name:
                return True, industry

    # 是效率工具但未匹配到具体行业, 归为 developer (默认, 因为 GitHub 上多为开发者工具)
    return True, "developer"


def detect_chinese_doc(item: Dict[str, Any]) -> bool:
    """检测是否有中文文档 (基于 description 中的中文信号)."""
    desc = item.get("description") or ""
    if not desc:
        return False
    # description 含中文字符
    if CJK_PATTERN.search(desc):
        return True
    # description 含中文文档关键词
    desc_lower = desc.lower()
    for kw in CHINESE_KEYWORDS:
        if kw in desc_lower:
            return True
    return False


# ===== 中国地区检测 =====
# location 中出现这些关键词视为中国开发者
CHINA_LOCATION_KEYWORDS = [
    "china", "beijing", "shanghai", "shenzhen", "guangzhou", "hangzhou",
    "chengdu", "nanjing", "wuhan", "xian", "中国", "北京", "上海",
    "深圳", "广州", "杭州", "成都", "南京", "武汉", "西安",
    "hong kong", "香港", "taiwan", "台湾", "taipei", "台北",
    "hubei", "湖北", "jiangsu", "江苏", "zhejiang", "浙江", "guangdong", "广东",
    "sichuan", "四川", "shandong", "山东", "fujian", "福建",
]

# owner login 中常见中国开发者后缀/前缀 (弱信号, 仅作辅助)
# 不基于 login 判定, 仅基于 location / company / bio


def detect_region_from_user(user_data: Optional[Dict[str, Any]]) -> Tuple[str, bool]:
    """根据 GitHub user API 返回的数据识别地区.

    Args:
        user_data: /users/{owner} 的响应, None 表示未获取

    Returns:
        (region, is_chinese): region ∈ {"china", "overseas", "unknown"}
    """
    if not user_data:
        return "unknown", False

    location = (user_data.get("location") or "").lower()
    company = (user_data.get("company") or "").lower()
    bio = (user_data.get("bio") or "").lower()

    text = f"{location} {company} {bio}"

    for kw in CHINA_LOCATION_KEYWORDS:
        if kw in text:
            return "china", True

    # 有 location 但不含中国关键词, 视为海外
    if location:
        return "overseas", False

    return "unknown", False
