import { useEffect, useMemo, useState } from 'react';
import { Segmented, Spin, message } from 'antd';
import { Link } from 'react-router-dom';
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { getRadar, getCategoryStats } from '../api/client';
import type { CategoryStat, Period, RadarPoint } from '../types';
import { useScatterReveal } from '../hooks/useScatterReveal';

// 分类中文映射
const CATEGORY_MAP: Record<string, string> = {
  ai: 'AI/机器学习',
  frontend: '前端',
  backend: '后端',
  devops: '运维/DevOps',
  security: '安全',
  database: '数据库',
  mobile: '移动开发',
  game: '游戏开发',
  data: '数据科学',
  blockchain: '区块链',
  iot: '物联网',
};

const CATEGORY_ORDER = [
  'ai', 'frontend', 'backend', 'devops', 'security',
  'database', 'mobile', 'game', 'data', 'blockchain', 'iot',
];

// 分类配色 — GH Pulse vibrant palette (matches --chart-* tokens)
const CATEGORY_COLORS: Record<string, string> = {
  ai: '#2f81f7',
  frontend: '#3fb950',
  backend: '#79c0ff',
  devops: '#d29922',
  security: '#bc8cff',
  database: '#56d364',
  mobile: '#a5d6ff',
  game: '#e3b341',
  data: '#d2a8ff',
  blockchain: '#7ee787',
  iot: '#f778ba',
};

const CATEGORY_CODES: Record<string, string> = {
  ai: 'AI', frontend: 'FE', backend: 'BE', devops: 'OPS',
  security: 'SEC', database: 'DB', mobile: 'MOB', game: 'GAME',
  data: 'DAT', blockchain: 'BC', iot: 'IOT',
};

function categoryLabel(category: string | null): string {
  if (!category) return '未分类';
  return CATEGORY_MAP[category] ?? '未分类';
}
function categoryCode(category: string | null): string {
  if (!category) return '—';
  return CATEGORY_CODES[category] ?? '???';
}
function categoryColor(category: string | null): string {
  if (!category) return 'var(--text-dim)';
  return CATEGORY_COLORS[category] ?? 'var(--accent)';
}
function categorySortIndex(category: string | null): number {
  if (!category) return CATEGORY_ORDER.length;
  const idx = CATEGORY_ORDER.indexOf(category);
  return idx === -1 ? CATEGORY_ORDER.length : idx;
}

interface ChartPoint {
  category: string;
  rawCategory: string | null;
  code: string;
  repos: number;
  stars_gained: number;
  total_stars: number;
  value: number;
}

export default function RadarPage() {
  const [period, setPeriod] = useState<Period>('weekly');
  const [radar, setRadar] = useState<RadarPoint[]>([]);
  const [categoryStats, setCategoryStats] = useState<CategoryStat[]>([]);
  const [loading, setLoading] = useState(true);
  // entrance animation — random scatter reveal of toolbar / radar chart / cards
  const { shown: shownReveal } = useScatterReveal(
    ['toolbar', 'radarChart', 'categoryCards', 'topRepos'],
    200,
  );

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [r, c] = await Promise.all([
          getRadar(period, 3),
          getCategoryStats(period),
        ]);
        if (cancelled) return;
        setRadar(r);
        setCategoryStats(c);
      } catch (e) {
        if (cancelled) return;
        message.error('加载技术雷达数据失败: ' + (e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [period]);

  const useTotalStars = period === 'all';

  const chartData: ChartPoint[] = useMemo(
    () =>
      radar
        .map((rp) => {
          const cs = categoryStats.find(
            (c) => (c.category ?? null) === (rp.category ?? null),
          );
          const value = useTotalStars ? cs?.total_stars ?? 0 : rp.stars_gained;
          return {
            category: categoryLabel(rp.category),
            rawCategory: rp.category,
            code: categoryCode(rp.category),
            repos: rp.repos,
            stars_gained: rp.stars_gained,
            total_stars: cs?.total_stars ?? 0,
            value,
          };
        })
        .sort(
          (a, b) =>
            categorySortIndex(a.rawCategory) - categorySortIndex(b.rawCategory),
        ),
    [radar, categoryStats, useTotalStars],
  );

  const sortedRadar = useMemo(
    () =>
      [...radar].sort(
        (a, b) => categorySortIndex(a.category) - categorySortIndex(b.category),
      ),
    [radar],
  );

  const totalRepos = radar.reduce((s, r) => s + r.repos, 0);
  const totalGained = radar.reduce((s, r) => s + r.stars_gained, 0);
  const totalStars = categoryStats.reduce((s, c) => s + c.total_stars, 0);
  const topCategory = [...radar].sort(
    (a, b) =>
      (useTotalStars
        ? categoryStats.find((c) => c.category === a.category)?.total_stars ?? 0
        : a.stars_gained) -
      (useTotalStars
        ? categoryStats.find((c) => c.category === b.category)?.total_stars ?? 0
        : b.stars_gained),
  )[0];

  if (loading && radar.length === 0) {
    return <Spin size="large" style={{ display: 'block', marginTop: 100 }} />;
  }

  const metricLabel = useTotalStars ? '累计 Star' : '新增 ★';
  const metricValue = useTotalStars ? totalStars : totalGained;
  const topValue = topCategory
    ? useTotalStars
      ? categoryStats.find((c) => c.category === topCategory.category)?.total_stars ?? 0
      : topCategory.stars_gained
    : 0;

  return (
    <div className="page-shell-loose anim-fade-up">
      {/* compact toolbar — period switcher + radar summary */}
      <div className={`page-toolbar scatter-item ${shownReveal.has('toolbar') ? 'is-shown' : ''}`}>
        <div className="page-toolbar-left">
          <span className="signal-chip">
            <span className="dot" />
            {totalRepos} REPOS
          </span>
          <span className="page-toolbar-meta">
            11 维度分布 · 按 {useTotalStars ? '累计 ★' : '周期增量'} 排序
          </span>
        </div>
        <Segmented
          value={period}
          onChange={(v) => setPeriod(v as Period)}
          options={[
            { label: '每日', value: 'daily' },
            { label: '每周', value: 'weekly' },
            { label: '每月', value: 'monthly' },
            { label: '全部', value: 'all' },
          ]}
        />
      </div>

      {/* ───────── HERO: radar chart + editorial summary ───────── */}
      <div
        className={`scatter-item list-fade ${shownReveal.has('radarChart') ? 'is-shown' : ''} ${loading && radar.length > 0 ? 'list-fade-out' : ''}`}
        style={{
          display: 'grid',
          gridTemplateColumns: '1.1fr 1fr',
          gap: 0,
          border: '1px solid var(--border)',
          background: 'var(--bg-elevated)',
          marginBottom: 24,
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-sm)',
          overflow: 'hidden',
        }}
      >
        {/* LEFT: radar chart */}
        <div
          style={{
            position: 'relative',
            padding: '24px 24px 16px',
            borderRight: '1px solid var(--border)',
          }}
        >
          <div className="label-mono" style={{ marginBottom: 4 }}>
            极坐标分布
          </div>
          <div
            className="serif"
            style={{
              fontSize: 18,
              color: 'var(--text)',
              marginBottom: 8,
              lineHeight: 1.3,
            }}
          >
            各领域 {useTotalStars ? '累计 Star' : '周期增量'} 的相对强度
          </div>

          <ResponsiveContainer width="100%" height={380}>
            <RadarChart data={chartData} outerRadius="72%">
              <PolarGrid stroke="var(--border-strong)" strokeDasharray="2 3" />
              <PolarAngleAxis
                dataKey="code"
                tick={{
                  fill: 'var(--text-muted)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 10,
                  letterSpacing: '0.08em',
                }}
              />
              <PolarRadiusAxis
                tick={{
                  fill: 'var(--text-dim)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 9,
                }}
                stroke="var(--border)"
              />
              <Radar
                name={metricLabel}
                dataKey="value"
                stroke="var(--accent)"
                strokeWidth={2}
                fill="var(--accent)"
                fillOpacity={0.25}
                dot={{
                  r: 4,
                  fill: 'var(--accent-bright)',
                  stroke: 'var(--bg-elevated)',
                  strokeWidth: 1.5,
                }}
                activeDot={{
                  r: 6,
                  fill: 'var(--accent-bright)',
                  stroke: 'var(--bg-elevated)',
                  strokeWidth: 2,
                }}
                animationBegin={200}
                animationDuration={1200}
                isAnimationActive
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const p = (payload[0]?.payload ?? {}) as Partial<ChartPoint>;
                  return (
                    <div
                      style={{
                        background: 'var(--bg-elevated)',
                        border: '1px solid var(--border-strong)',
                        padding: '10px 12px',
                        fontFamily: 'var(--font-mono)',
                        fontSize: 11,
                        minWidth: 170,
                      }}
                    >
                      <div
                        className="serif"
                        style={{
                          fontSize: 14,
                          color: 'var(--text)',
                          marginBottom: 6,
                          fontStyle: 'normal',
                          fontFamily: 'var(--font-sans)',
                          fontWeight: 600,
                        }}
                      >
                        {p.category}
                      </div>
                      <div
                        style={{
                          color: 'var(--accent)',
                          fontWeight: 700,
                          letterSpacing: '0.04em',
                          marginBottom: 4,
                        }}
                      >
                        {Number(payload[0]?.value ?? 0).toLocaleString()} ★
                      </div>
                      <div style={{ color: 'var(--text-dim)', fontSize: 10 }}>
                        {p.repos ?? 0} 仓库 · 新增 +{Number(p.stars_gained ?? 0).toLocaleString()}
                      </div>
                    </div>
                  );
                }}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        {/* RIGHT: editorial summary */}
        <div style={{ padding: '32px 36px', position: 'relative' }}>
          <div className="eyebrow" style={{ marginBottom: 14 }}>
            本期观察
          </div>

          <div
            className="serif"
            style={{
              fontSize: 24,
              lineHeight: 1.4,
              color: 'var(--text)',
              marginBottom: 24,
            }}
          >
            信号最强的领域是
            <span style={{ color: topCategory ? categoryColor(topCategory.category) : 'var(--accent)' }}>
              {' '}{topCategory ? categoryLabel(topCategory.category) : '—'}
            </span>
            ，{useTotalStars ? '累计' : '本期新增'}
            <span style={{ color: 'var(--accent)' }}>
              {' '}{topValue.toLocaleString()} ★
            </span>
            。
          </div>

          <div style={{ display: 'flex', alignItems: 'baseline', gap: 24, flexWrap: 'wrap' }}>
            <div>
              <div className="label-mono">{metricLabel}</div>
              <div className="editorial-number" style={{ color: 'var(--accent)', fontSize: 54 }}>
                {metricValue.toLocaleString()}
              </div>
            </div>
            <div style={{ height: 54, width: 1, background: 'var(--border)', alignSelf: 'center' }} />
            <div>
              <div className="label-mono">追踪仓库</div>
              <div className="numeric" style={{ fontSize: 24, fontWeight: 700, marginTop: 6, color: 'var(--text)' }}>
                {totalRepos.toLocaleString()}
              </div>
            </div>
            <div style={{ height: 54, width: 1, background: 'var(--border)', alignSelf: 'center' }} />
            <div>
              <div className="label-mono">活跃分类</div>
              <div className="numeric" style={{ fontSize: 24, fontWeight: 700, marginTop: 6, color: 'var(--sage)' }}>
                {radar.length}
              </div>
            </div>
          </div>

          <div
            className="mono"
            style={{
              position: 'absolute',
              bottom: 16,
              right: 20,
              fontSize: 10,
              letterSpacing: '0.18em',
              color: 'var(--text-dim)',
              textTransform: 'uppercase',
            }}
          >
            周期 / {String(period).toUpperCase()}
          </div>
        </div>
      </div>

      {/* ───────── METRIC STRIP ───────── */}
      <div className="metric-strip" style={{ marginBottom: 24 }}>
        <div>
          <div className="metric-label">总仓库</div>
          <div className="metric-value metric-value-cyan">{totalRepos.toLocaleString()}</div>
        </div>
        <div>
          <div className="metric-label">总 Star</div>
          <div className="metric-value metric-value-amber">{totalStars.toLocaleString()}</div>
        </div>
        <div>
          <div className="metric-label">新增 ★</div>
          <div className="metric-value metric-value-hot">+{totalGained.toLocaleString()}</div>
        </div>
        <div>
          <div className="metric-label">分类数</div>
          <div className="metric-value metric-value-cyan">{radar.length}</div>
        </div>
        <div>
          <div className="metric-label">最强信号</div>
          <div
            className="numeric"
            style={{
              fontSize: 18,
              fontWeight: 700,
              marginTop: 6,
              color: 'var(--text)',
              letterSpacing: '0.04em',
            }}
          >
            {topCategory ? categoryCode(topCategory.category) : '—'}
          </div>
        </div>
        <div>
          <div className="metric-label">扫描周期</div>
          <div
            className="mono"
            style={{
              fontSize: 14,
              fontWeight: 700,
              marginTop: 6,
              color: 'var(--text)',
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
            }}
          >
            {period}
          </div>
        </div>
      </div>

      {/* ───────── DIVIDER ───────── */}
      <div className={`scatter-item list-fade ${shownReveal.has('categoryCards') ? 'is-shown' : ''} ${loading && radar.length > 0 ? 'list-fade-out' : ''}`}>
        <div className="divider-mono">
          各分类 Top 3 仓库
        </div>

      {/* ───────── CATEGORY CARDS ───────── */}
      {sortedRadar.length === 0 ? (
        <div
          className="mono"
          style={{
            border: '1px solid var(--border)',
            background: 'var(--bg-elevated)',
            padding: '40px',
            textAlign: 'center',
            color: 'var(--text-dim)',
            fontSize: 12,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            borderRadius: 'var(--radius-lg)',
          }}
        >
          暂无数据
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))',
            gap: 16,
          }}
        >
          {sortedRadar.map((rp, idx) => {
            const color = categoryColor(rp.category);
            const cs = categoryStats.find((c) => (c.category ?? null) === (rp.category ?? null));
            const totalStarsCat = cs?.total_stars ?? 0;
            const cardValue = useTotalStars ? totalStarsCat : rp.stars_gained;
            const maxValue = Math.max(
              ...sortedRadar.map((r) =>
                useTotalStars
                  ? categoryStats.find((c) => c.category === r.category)?.total_stars ?? 0
                  : r.stars_gained,
              ),
              1,
            );
            const barWidth = (cardValue / maxValue) * 100;
            return (
              <div
                key={rp.category ?? 'uncategorized'}
                style={{
                  position: 'relative',
                  border: '1px solid var(--border)',
                  background: 'var(--bg-elevated)',
                  overflow: 'hidden',
                  borderRadius: 'var(--radius-lg)',
                  boxShadow: 'var(--shadow-sm)',
                  transition: 'border-color 200ms ease, box-shadow 200ms ease, transform 200ms ease',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = color;
                  (e.currentTarget as HTMLElement).style.boxShadow = 'var(--shadow-md)';
                  (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)';
                  (e.currentTarget as HTMLElement).style.boxShadow = 'var(--shadow-sm)';
                  (e.currentTarget as HTMLElement).style.transform = 'translateY(0)';
                }}
              >
                {/* card header */}
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'flex-start',
                    padding: '14px 18px 12px',
                    borderBottom: '1px solid var(--border)',
                  }}
                >
                  <div>
                    <div className="label-mono" style={{ fontSize: 9 }}>
                      {String(idx + 1).padStart(2, '0')} · {categoryCode(rp.category)}
                    </div>
                    <div
                      style={{
                        fontSize: 15,
                        fontWeight: 600,
                        color: 'var(--text)',
                        marginTop: 4,
                        fontFamily: 'var(--font-sans)',
                      }}
                    >
                      {categoryLabel(rp.category)}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div className="label-mono" style={{ fontSize: 9 }}>
                      {rp.repos} 仓库
                    </div>
                    <div
                      className="numeric"
                      style={{
                        fontSize: 22,
                        fontWeight: 700,
                        color: 'var(--accent)',
                        marginTop: 4,
                        lineHeight: 1,
                      }}
                    >
                      {cardValue.toLocaleString()}
                    </div>
                    <div className="label-mono" style={{ fontSize: 9, marginTop: 2 }}>
                      {useTotalStars ? '累计 ★' : '+ 新增 ★'}
                    </div>
                  </div>
                </div>

                {/* signal bar */}
                <div style={{ height: 2, background: 'var(--surface-2)', position: 'relative' }}>
                  <div
                    style={{
                      position: 'absolute',
                      left: 0,
                      top: 0,
                      bottom: 0,
                      width: `${barWidth}%`,
                      background: color,
                      transition: 'width 600ms cubic-bezier(0.2, 0.7, 0.2, 1)',
                    }}
                  />
                </div>

                {/* top repos list */}
                <div>
                  {rp.top_repos.map((tr, i) => (
                    <div
                      key={tr.repo.id}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        padding: '10px 18px',
                        borderBottom: i < rp.top_repos.length - 1 ? '1px solid var(--border)' : 'none',
                        gap: 12,
                        transition: 'background 160ms ease',
                      }}
                      onMouseEnter={(e) => {
                        (e.currentTarget as HTMLElement).style.background = 'var(--accent-tint)';
                      }}
                      onMouseLeave={(e) => {
                        (e.currentTarget as HTMLElement).style.background = 'transparent';
                      }}
                    >
                      <span
                        className="numeric"
                        style={{
                          width: 22,
                          fontSize: 11,
                          fontWeight: 700,
                          color: i === 0 ? color : 'var(--text-dim)',
                          flexShrink: 0,
                        }}
                      >
                        {String(i + 1).padStart(2, '0')}
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <Link
                          to={`/repos/${tr.repo.id}`}
                          style={{
                            fontSize: 13,
                            fontWeight: 500,
                            color: 'var(--text)',
                            display: 'block',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            border: 'none',
                          }}
                        >
                          {tr.repo.full_name}
                        </Link>
                        <div
                          className="mono"
                          style={{
                            fontSize: 10,
                            color: 'var(--text-dim)',
                            marginTop: 3,
                            letterSpacing: '0.04em',
                            display: 'flex',
                            gap: 10,
                            flexWrap: 'wrap',
                          }}
                        >
                          <span style={{ color: 'var(--accent)', fontWeight: 600 }}>
                            +{tr.metric_value.toLocaleString()}
                          </span>
                          <span>★ {tr.repo.stargazers_count.toLocaleString()}</span>
                          {tr.repo.language && (
                            <span style={{ color: 'var(--slate)' }}>{tr.repo.language}</span>
                          )}
                        </div>
                      </div>
                      <a
                        href={tr.repo.html_url}
                        target="_blank"
                        rel="noreferrer"
                        className="mono"
                        style={{
                          fontSize: 13,
                          color: 'var(--text-dim)',
                          flexShrink: 0,
                          padding: '0 4px',
                          border: 'none',
                        }}
                      >
                        ↗
                      </a>
                    </div>
                  ))}
                  {rp.top_repos.length === 0 && (
                    <div
                      className="mono"
                      style={{
                        padding: '24px 18px',
                        fontSize: 10,
                        color: 'var(--text-dim)',
                        letterSpacing: '0.16em',
                        textTransform: 'uppercase',
                        textAlign: 'center',
                      }}
                    >
                      暂无仓库
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      </div>

      {/* footer note */}
      <div
        className={`scatter-item ${shownReveal.has('topRepos') ? 'is-shown' : ''} mono`}
        style={{
          marginTop: 24,
          marginBottom: 8,
          fontSize: 10,
          letterSpacing: '0.16em',
          color: 'var(--text-dim)',
          textTransform: 'uppercase',
          display: 'flex',
          gap: 18,
          flexWrap: 'wrap',
        }}
      >
        <span><span style={{ color: 'var(--sage)' }}>●</span> 活跃</span>
        <span><span style={{ color: 'var(--accent)' }}>●</span> {radar.length} 分类</span>
        <span><span style={{ color: 'var(--slate)' }}>●</span> {totalRepos} 仓库</span>
        <span><span style={{ color: 'var(--ochre)' }}>●</span> 周期 {String(period).toUpperCase()}</span>
      </div>
    </div>
  );
}
