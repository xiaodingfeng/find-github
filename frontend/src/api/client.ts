import axios from 'axios';
import type {
  AIInterpretation,
  CategoryStat,
  CrawlRun,
  CrawlRunList,
  IndustryStat,
  InterpretResult,
  LanguageStat,
  Period,
  RadarPoint,
  Repository,
  RepositoryDetail,
  RepositoryList,
  RepoFilter,
  Summary,
  TimelinePoint,
  TopRepo,
} from '../types';

const client = axios.create({
  baseURL: '/api',
  timeout: 30000,
});

// ===== 管理员 token 存储 (localStorage, 跨标签页/浏览器重启共享) =====
const ADMIN_TOKEN_KEY = 'find-github-admin-token';

/**
 * 解析 token payload 中的 exp (过期时间, unix 秒). token 格式:
 * b64url(payload_json) + "." + b64url(signature), payload = {"exp": ..., "role": ...}
 * 解析失败返回 null.
 */
function getTokenExp(token: string): number | null {
  const dotIdx = token.indexOf('.');
  if (dotIdx < 0) return null;
  try {
    const body = token.slice(0, dotIdx);
    // base64url → base64 (补 padding)
    const b64 = body.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((body.length + 3) % 4);
    const payload = JSON.parse(atob(b64));
    const exp = payload?.exp;
    return typeof exp === 'number' ? exp : null;
  } catch {
    return null;
  }
}

export function getAdminToken(): string | null {
  try {
    const token = localStorage.getItem(ADMIN_TOKEN_KEY);
    if (!token) return null;
    // 前端兜底: 若已过期则清除, 避免携带失效 token 发请求
    const exp = getTokenExp(token);
    if (exp !== null && exp < Math.floor(Date.now() / 1000)) {
      localStorage.removeItem(ADMIN_TOKEN_KEY);
      return null;
    }
    return token;
  } catch {
    return null;
  }
}

export function setAdminToken(token: string): void {
  try {
    localStorage.setItem(ADMIN_TOKEN_KEY, token);
    window.dispatchEvent(new Event('admin-token-change'));
  } catch {
    /* localStorage 不可用时静默 */
  }
}

export function clearAdminToken(): void {
  try {
    localStorage.removeItem(ADMIN_TOKEN_KEY);
    window.dispatchEvent(new Event('admin-token-change'));
  } catch {
    /* ignore */
  }
}

// 请求拦截器: 携带签名 token (后端仅在敏感端点校验)
client.interceptors.request.use((config) => {
  const token = getAdminToken();
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截器: 401 时清除失效 token (过期/被篡改)
client.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error?.response?.status === 401) {
      clearAdminToken();
    }
    return Promise.reject(error);
  },
);

export async function getRepos(filter: RepoFilter): Promise<RepositoryList> {
  const { data } = await client.get<RepositoryList>('/repos', { params: filter });
  return data;
}

export async function getRepo(id: number): Promise<RepositoryDetail> {
  const { data } = await client.get<RepositoryDetail>(`/repos/${id}`);
  return data;
}

export async function getRepoSnapshots(id: number) {
  const { data } = await client.get(`/repos/${id}/snapshots`);
  return data;
}

export async function getRepoInterpretations(id: number): Promise<AIInterpretation[]> {
  const { data } = await client.get<AIInterpretation[]>(`/repos/${id}/interpretation`);
  return data;
}

export async function getRepoReadme(id: number): Promise<{ content: string; cached: boolean }> {
  // README 内容可能较长, 单独放宽超时
  const { data } = await client.get<{ content: string; cached: boolean }>(
    `/repos/${id}/readme`,
    { timeout: 30000 },
  );
  return data;
}

export async function translateRepoReadme(id: number): Promise<{ content: string; cached: boolean }> {
  // AI 翻译可能耗时较长 (LLM 生成), 放宽超时到 3 分钟
  const { data } = await client.post<{ content: string; cached: boolean }>(
    `/repos/${id}/translate-readme`,
    {},
    { timeout: 180000 },
  );
  return data;
}

export async function getSummary(): Promise<Summary> {
  const { data } = await client.get<Summary>('/stats/summary');
  return data;
}

export async function getLanguages(limit = 20, period?: Period): Promise<LanguageStat[]> {
  const { data } = await client.get<LanguageStat[]>('/stats/languages', {
    params: { limit, period: period === 'all' ? undefined : period },
  });
  return data;
}

export async function getTopRepos(
  period?: Period,
  limit = 10,
  metric: 'stars' | 'forks' = 'stars',
): Promise<TopRepo[]> {
  const { data } = await client.get<TopRepo[]>('/stats/top', {
    params: { limit, metric, period: period === 'all' ? undefined : period },
  });
  return data;
}

export async function getTimeline(days = 30): Promise<TimelinePoint[]> {
  const { data } = await client.get<TimelinePoint[]>('/stats/timeline', {
    params: { days },
  });
  return data;
}

export async function getCategoryStats(period?: Period): Promise<CategoryStat[]> {
  const { data } = await client.get<CategoryStat[]>('/stats/categories', {
    params: { period: period === 'all' ? undefined : period },
  });
  return data;
}

export async function getRadar(period?: Period, topPerCategory = 3): Promise<RadarPoint[]> {
  const { data } = await client.get<RadarPoint[]>('/stats/radar', {
    params: { period: period === 'all' ? undefined : period, top_per_category: topPerCategory },
  });
  return data;
}

export async function getIndustryStats(period?: Period): Promise<IndustryStat[]> {
  const { data } = await client.get<IndustryStat[]>('/stats/industries', {
    params: { period: period === 'all' ? undefined : period },
  });
  return data;
}

export async function getRuns(
  params: { period?: Period; status?: string; page?: number; per_page?: number } = {},
): Promise<CrawlRunList> {
  const { data } = await client.get<CrawlRunList>('/runs', { params });
  return data;
}

export async function triggerCrawl(
  period: 'daily' | 'weekly' | 'monthly',
  threshold?: number,
) {
  const { data } = await client.post('/runs/trigger', { period, threshold });
  return data as { run_id: number; status: string; message: string };
}

export async function cancelRun(runId: number): Promise<{ run_id: number; status: string; message: string }> {
  const { data } = await client.post(`/runs/${runId}/cancel`);
  return data;
}

export async function triggerInterpret(
  repoIds?: number[],
  limit = 50,
  force = false,
): Promise<InterpretResult> {
  const { data } = await client.post<InterpretResult>('/interpret', {
    repo_ids: repoIds,
    limit,
    force,
  });
  return data;
}

export async function triggerInterpretSync(
  repoIds?: number[],
  limit = 50,
  force = false,
): Promise<InterpretResult> {
  // 同步生成, 后端阻塞直到完成. 超时设大一些 (单个仓库约 5-15 秒)
  const { data } = await client.post<InterpretResult>('/interpret/sync', {
    repo_ids: repoIds,
    limit,
    force,
  }, { timeout: 300000 });
  return data;
}

export async function triggerInterpretAll(): Promise<InterpretResult> {
  // 全量 AI 解读 (后台线程异步执行, 并行度由后端 LLM_CONCURRENCY 控制)
  const { data } = await client.post<InterpretResult>('/interpret/all');
  return data;
}

export interface InterpretProgress {
  running: boolean;
  total: number;
  success: number;
  failed: number;
  processed: number;
  progress_percent: number;
  success_rate: number;
  current_repo: string;
  started_at: string;
  finished_at: string;
  error: string;
}

export async function getInterpretProgress(): Promise<InterpretProgress> {
  const { data } = await client.get<InterpretProgress>('/interpret/progress');
  return data;
}

export async function verifyAdmin(
  password: string,
): Promise<{ ok: boolean; message: string; token?: string }> {
  const { data } = await client.post<{ ok: boolean; message: string; token?: string }>(
    '/auth/verify',
    { password },
  );
  // 验证通过则持久化 token, 后续敏感请求自动携带
  if (data.ok && data.token) {
    setAdminToken(data.token);
  }
  return data;
}

export async function getHealth() {
  const { data } = await client.get('/health');
  return data;
}

// 收藏 (本地存储)
const FAV_KEY = 'find-github-favorites';

export function getFavorites(): number[] {
  try {
    const raw = localStorage.getItem(FAV_KEY);
    return raw ? (JSON.parse(raw) as number[]) : [];
  } catch {
    return [];
  }
}

export function toggleFavorite(id: number): boolean {
  const favs = new Set(getFavorites());
  let added: boolean;
  if (favs.has(id)) {
    favs.delete(id);
    added = false;
  } else {
    favs.add(id);
    added = true;
  }
  localStorage.setItem(FAV_KEY, JSON.stringify(Array.from(favs)));
  return added;
}

export function isFavorite(id: number): boolean {
  return getFavorites().includes(id);
}

export type { Repository, RepositoryDetail, CrawlRun };
