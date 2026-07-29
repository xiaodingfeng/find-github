export interface Repository {
  id: number;
  github_id: number;
  name: string;
  full_name: string;
  owner: string;
  owner_type: string | null;
  html_url: string;
  description: string | null;
  language: string | null;
  topics: string[];
  license: string | null;
  stargazers_count: number;
  forks_count: number;
  watchers_count: number;
  open_issues_count: number;
  created_at: string | null;
  updated_at: string | null;
  pushed_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  // 产品扩展字段
  region: string;
  is_chinese_owner: boolean;
  has_chinese_doc: boolean;
  category: string | null;
  is_efficiency_tool: boolean;
  industry: string | null;
  // 列表场景附加字段 (后端按 period 计算后返回)
  stars_gained?: number | null;
  latest_interpretation?: AIInterpretation | null;
}

export interface Snapshot {
  id: number;
  repository_id: number;
  snapshot_date: string;
  period: 'daily' | 'weekly' | 'monthly';
  stars_at_snapshot: number;
  forks_at_snapshot: number;
  stars_gained: number;
  rank_in_period: number | null;
  crawl_run_id: number | null;
}

export interface AIInterpretation {
  id: number;
  repository_id: number;
  summary_cn: string | null;
  value_prop: string | null;
  difficulty: number | null;
  learning_hours: number | null;
  suitable_for: string | null;
  alternatives: string | null;
  model: string | null;
  generated_at: string;
}

export interface RepositoryDetail extends Repository {
  snapshots: Snapshot[];
  interpretations: AIInterpretation[];
}

export interface RepositoryAggregates {
  total_stars: number;
  total_forks: number;
  total_gained: number;
  chinese_count: number;
  interpreted_count: number;
}

export interface RepositoryList {
  items: Repository[];
  total: number;
  page: number;
  per_page: number;
  aggregates?: RepositoryAggregates | null;
}

export interface CrawlRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  period: 'daily' | 'weekly' | 'monthly';
  status: 'running' | 'success' | 'failed';
  total_repos_found: number;
  total_repos_upserted: number;
  error_message: string | null;
  params: Record<string, unknown> | null;
}

export interface CrawlRunList {
  items: CrawlRun[];
  total: number;
}

export interface Summary {
  total_repos: number;
  total_runs: number;
  by_period: Record<string, number>;
  recent_runs: CrawlRun[];
  avg_stars: number;
  top_language: string | null;
  chinese_repos: number;
  efficiency_tools: number;
  interpreted_repos: number;
}

export interface LanguageStat {
  language: string | null;
  count: number;
}

export interface TopRepo {
  repo: Repository;
  metric_value: number;
}

export interface TimelinePoint {
  date: string;
  period: 'daily' | 'weekly' | 'monthly';
  count: number;
}

export interface CategoryStat {
  category: string | null;
  count: number;
  total_stars: number;
  total_stars_gained: number;
}

export interface RadarPoint {
  category: string;
  repos: number;
  stars_gained: number;
  top_repos: TopRepo[];
}

export interface IndustryStat {
  industry: string | null;
  count: number;
  total_stars: number;
}

export interface InterpretResult {
  total: number;
  success: number;
  failed: number;
}

export type Period = 'daily' | 'weekly' | 'monthly' | 'all';
export type SortField = 'stars' | 'forks' | 'updated' | 'created' | 'pushed' | 'name' | 'gained';
export type SortOrder = 'asc' | 'desc';

export interface RepoFilter {
  period?: Period;
  language?: string;
  min_stars?: number;
  max_stars?: number;
  topic?: string;
  license?: string;
  q?: string;
  sort?: SortField;
  order?: SortOrder;
  page?: number;
  per_page?: number;
  // 产品扩展筛选
  region?: 'china' | 'overseas' | 'unknown';
  category?: string;
  is_chinese_doc?: boolean;
  is_efficiency_tool?: boolean;
  industry?: string;
  // 逗号分隔的仓库 ID, 后端按此列表过滤 (前端"仅看收藏"跨页筛选)
  favorite_ids?: string;
}
