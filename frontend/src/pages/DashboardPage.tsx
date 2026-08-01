import { useEffect, useRef, useState } from 'react';
import { Card, Col, Row, Segmented, Space, Table, Tag, Typography, message } from 'antd';
import { Tooltip as AntTooltip } from 'antd';
import { RobotOutlined, RiseOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { formatCompact } from '../utils';
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip,
  Legend,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
} from 'recharts';
import {
  getSummary,
  getLanguages,
  getTopRepos,
  getTimeline,
} from '../api/client';
import type { LanguageStat, Summary, TimelinePoint, TopRepo } from '../types';
import Sparkline from '../components/Sparkline';
import { useScatterReveal } from '../hooks/useScatterReveal';

const PIE_COLORS = [
  '#4263eb', '#2f9e44', '#9c36b5', '#e67700', '#d6336c',
  '#5c7cfa', '#37b24d', '#e64980', '#f08c00', '#60a5fa',
  '#e8590c', '#ae3ec9', '#40c057', '#fd7e14', '#f06513',
];

const { Text, Paragraph } = Typography;

/* ───────── animated count-up hook ───────── */
function useCountUp(target: number, duration = 1200, deps: unknown[] = []) {
  const [value, setValue] = useState(0);
  const rafRef = useRef<number | null>(null);
  useEffect(() => {
    if (target === 0) {
      setValue(0);
      return;
    }
    const start = performance.now();
    const from = 0;
    const animate = (now: number) => {
      const elapsed = now - start;
      const t = Math.min(elapsed / duration, 1);
      // easeOutExpo
      const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
      setValue(Math.round(from + (target - from) * eased));
      if (t < 1) rafRef.current = requestAnimationFrame(animate);
    };
    rafRef.current = requestAnimationFrame(animate);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, duration, ...deps]);
  return value;
}

/* ───────── animated stat tile ───────── */
function StatHero({
  value,
  label,
  color = 'var(--text)',
  suffix = '',
  subs,
}: {
  value: number;
  label: string;
  color?: string;
  suffix?: string;
  subs?: { label: string; value: string }[];
}) {
  const animated = useCountUp(value);
  return (
    <div style={{ position: 'relative' }}>
      <div
        className="label-mono"
        style={{ marginBottom: 6, color: 'var(--text-dim)' }}
      >
        {label}
      </div>
      <div
        className="display"
        style={{
          fontSize: 'clamp(26px, 2.8vw, 38px)',
          fontWeight: 700,
          lineHeight: 1,
          letterSpacing: '-0.04em',
          fontVariantNumeric: 'tabular-nums',
          color,
        }}
      >
        {animated.toLocaleString()}
        {suffix && (
          <span style={{ fontSize: '0.5em', marginLeft: 4, opacity: 0.7 }}>{suffix}</span>
        )}
      </div>
      {/* sub-stats fill the blank space under the big number */}
      {subs && subs.length > 0 && (
        <div style={{ display: 'flex', gap: 12, marginTop: 8, flexWrap: 'wrap' }}>
          {subs.map((s) => (
            <div key={s.label} style={{ fontSize: 11, lineHeight: 1.3 }}>
              <span style={{ color: 'var(--text-dim)' }}>{s.label} </span>
              <span className="mono" style={{ color: 'var(--text-muted)', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                {s.value}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ───────── radar scan visual — concentric rings + sweeping beam ───────── */
function RadarScan({ size = 132, big = false }: { size?: number; big?: boolean }) {
  return (
    <div
      className={`radar-scan ${big ? 'radar-scan-big' : ''}`}
      style={{
        width: size,
        height: size,
        flexShrink: 0,
        background: big
          ? 'radial-gradient(circle at 50% 50%, rgba(66, 99, 235, 0.12), transparent 70%)'
          : 'radial-gradient(circle at 50% 50%, rgba(66, 99, 235, 0.06), transparent 70%)',
        boxShadow: big
          ? 'inset 0 0 60px rgba(66, 99, 235, 0.14), 0 0 80px rgba(66, 99, 235, 0.10)'
          : 'inset 0 0 24px rgba(66, 99, 235, 0.08), var(--shadow-sm)',
      }}
    >
      <div className="radar-ring" />
      <div className="radar-ring radar-ring-2" />
      <div className="radar-ring radar-ring-3" />
      <div className="radar-ring radar-ring-4" />
      <div className="radar-cross-h" />
      <div className="radar-cross-v" />
      <div className="radar-sweep" />
      <div className="radar-sweep-line" />
      <div className="radar-blip" style={{ top: '28%', left: '62%' }} />
      <div className="radar-blip radar-blip-2" style={{ top: '58%', left: '38%' }} />
      <div className="radar-blip radar-blip-3" style={{ top: '44%', left: '74%' }} />
    </div>
  );
}

export default function DashboardPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [languages, setLanguages] = useState<LanguageStat[]>([]);
  const [topRepos, setTopRepos] = useState<TopRepo[]>([]);
  const [timeline, setTimeline] = useState<TimelinePoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [trendPeriod, setTrendPeriod] = useState<'daily' | 'weekly' | 'monthly'>('weekly');
  // trendLoading — drives the fade out/in transition when switching periods.
  // Only triggers fade when we already have data (not on initial load).
  const [trendLoading, setTrendLoading] = useState(false);
  // random-order scatter reveal — IDs are shuffled and each appears with an
  // irregular 100-400ms gap. radarBg is excluded so it stays as the first
  // thing visible; the rest trickle in.
  const DASHBOARD_IDS = [
    'radarBg', 'stat1', 'stat2', 'stat3', 'stat4', 'metaLine',
    'trend1', 'trend2', 'trend3', 'trend4',
    'charts', 'trending',
  ];
  const { shown, reveal } = useScatterReveal(DASHBOARD_IDS, 600);
  const initialLoadDone = useRef(false);

  async function loadAll() {
    setLoading(true);
    try {
      const [s, langs, top, tl] = await Promise.all([
        getSummary(),
        getLanguages(15),
        getTopRepos(trendPeriod, 10, 'stars'),
        getTimeline(30),
      ]);
      setSummary(s);
      setLanguages(langs);
      setTopRepos(top);
      setTimeline(tl);
    } catch (e) {
      message.error('加载数据失败: ' + (e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
    initialLoadDone.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!initialLoadDone.current) return;
    setTrendLoading(true);
    getTopRepos(trendPeriod, 10, 'stars')
      .then(setTopRepos)
      .catch((e) => message.error('加载趋势仓库失败: ' + (e as Error).message))
      .finally(() => setTrendLoading(false));
  }, [trendPeriod]);

  // while loading and no data, show only the radar overlay (no Spin)
  if (loading && !summary) {
    return (
      <div className="dashboard-loader">
        <RadarScan size={320} big />
        <div className="dashboard-loader-label">SCANNING GITHUB PULSE</div>
      </div>
    );
  }

  type TimelineBucket = { date: string; daily?: number; weekly?: number; monthly?: number };
  const timelineData = timeline.reduce<Record<string, TimelineBucket>>((acc, p) => {
    if (!acc[p.date]) acc[p.date] = { date: p.date };
    acc[p.date][p.period] = p.count;
    return acc;
  }, {});
  const timelineArr = Object.values(timelineData).sort((a, b) =>
    a.date < b.date ? -1 : 1,
  );

  // sparkline data derived from timeline — last 14 points per period
  const dailySeries = timelineArr.slice(-14).map((t) => t.daily ?? 0);
  const weeklySeries = timelineArr.slice(-14).map((t) => t.weekly ?? 0);
  const monthlySeries = timelineArr.slice(-14).map((t) => t.monthly ?? 0);
  const totalSeries = timelineArr.slice(-14).map((t) => (t.daily ?? 0) + (t.weekly ?? 0) + (t.monthly ?? 0));

  // day-over-day delta for trend tiles (last vs second-to-last nonzero)
  function calcDelta(series: number[]): { delta: number; pct: string } | null {
    const nonzero = series.filter((v) => v > 0);
    if (nonzero.length < 2) return null;
    const last = nonzero[nonzero.length - 1];
    const prev = nonzero[nonzero.length - 2];
    const delta = last - prev;
    const pct = prev > 0 ? `${delta >= 0 ? '+' : ''}${((delta / prev) * 100).toFixed(1)}%` : '—';
    return { delta, pct };
  }
  const dailyDelta = calcDelta(dailySeries);
  const weeklyDelta = calcDelta(weeklySeries);
  const monthlyDelta = calcDelta(monthlySeries);
  const totalDelta = calcDelta(totalSeries);

  // language chart: top N main slices + "Others" grouped from the rest.
  // LANG_TOP_N matches the title "Top 15" — show all 15 slices; only add an
  // "Others" bucket when the API returned more than 15 entries.
  const LANG_TOP_N = 15;
  const OTHERS_COLOR = '#adb5bd';
  const totalLangCount = languages.reduce((s, l) => s + l.count, 0);
  const othersCount = languages.slice(LANG_TOP_N).reduce((s, l) => s + l.count, 0);
  const langChartData = totalLangCount > 0
    ? [
        ...languages.slice(0, LANG_TOP_N),
        ...(othersCount > 0 ? [{ language: 'Others', count: othersCount }] : []),
      ]
    : [];
  const langLegend = langChartData.map((l, i) => ({
    name: l.language || '未知',
    color: i < LANG_TOP_N ? PIE_COLORS[i % PIE_COLORS.length] : OTHERS_COLOR,
    percent: totalLangCount > 0 ? ((l.count / totalLangCount) * 100).toFixed(1) : '0.0',
  }));

  // timeline: show all 30 days for a rich, full chart (was only last 2)
  const timelineFull = timelineArr;

  // rank medal colors
  const rankColors = ['#d29922', '#b1bac4', '#db6d28'];

  return (
    <div className="dashboard-root">
      {/* ───────── HERO · stats overlaid on giant radar background ───────── */}
      <div
        className="dashboard-hero"
        style={{
          padding: '14px 0 12px',
          marginBottom: 12,
          borderBottom: '1px solid var(--border)',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {/* glow halo */}
        <div
          style={{
            position: 'absolute',
            top: '-50%',
            left: '-10%',
            width: '60%',
            height: '200%',
            background: 'radial-gradient(circle, rgba(66, 99, 235, 0.10), transparent 60%)',
            filter: 'blur(40px)',
            pointerEvents: 'none',
            animation: 'orb-drift-1 22s ease-in-out infinite alternate',
          }}
        />

        {/* giant radar as background — fades in first, then stats scatter
            on top of it one-by-one. */}
        <div
          className="hero-radar-bg"
          data-shown={shown.has('radarBg')}
          style={{
            position: 'absolute',
            top: '50%',
            right: '-80px',
            transform: 'translateY(-50%)',
            opacity: shown.has('radarBg') ? 0.55 : 0,
            transition: 'opacity 800ms ease',
            pointerEvents: 'none',
          }}
        >
          <RadarScan size={300} big />
        </div>

        <div className="eyebrow" style={{ marginBottom: 10, position: 'relative' }}>
          § 01 / LIVE PULSE · {new Date().toISOString().slice(0, 10)}
        </div>

        {/* hero stat row — 4 animated counters with sub-stats, each reveals independently */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
            gap: 18,
            position: 'relative',
            zIndex: 2,
          }}
        >
          <div className={`scatter-item ${shown.has('stat1') ? 'is-shown' : ''}`}>
            <StatHero
              value={summary?.total_repos ?? 0}
              label="追踪仓库"
              color="var(--accent)"
              subs={[
                { label: '总★', value: summary ? formatCompact(summary.avg_stars * summary.total_repos) : '—' },
                { label: '均★', value: summary ? formatCompact(summary.avg_stars) : '—' },
              ]}
            />
          </div>
          <div className={`scatter-item ${shown.has('stat2') ? 'is-shown' : ''}`}>
            <StatHero
              value={summary?.chinese_repos ?? 0}
              label="国产开源"
              color="var(--accent)"
              subs={[
                { label: '占比', value: summary && summary.total_repos > 0 ? `${((summary.chinese_repos / summary.total_repos) * 100).toFixed(1)}%` : '—' },
              ]}
            />
          </div>
          <div className={`scatter-item ${shown.has('stat3') ? 'is-shown' : ''}`}>
            <StatHero
              value={summary?.efficiency_tools ?? 0}
              label="效率工具"
              color="var(--accent)"
              subs={[
                { label: '占比', value: summary && summary.total_repos > 0 ? `${((summary.efficiency_tools / summary.total_repos) * 100).toFixed(1)}%` : '—' },
              ]}
            />
          </div>
          <div className={`scatter-item ${shown.has('stat4') ? 'is-shown' : ''}`}>
            <StatHero
              value={summary?.interpreted_repos ?? 0}
              label="AI 解读"
              color="var(--accent)"
              subs={[
                { label: '覆盖率', value: summary && summary.total_repos > 0 ? `${((summary.interpreted_repos / summary.total_repos) * 100).toFixed(1)}%` : '—' },
                { label: '模型', value: 'auto' },
              ]}
            />
          </div>
        </div>

        {/* supporting meta line — single compact row, no wrap */}
        <div
          className={`scatter-item ${shown.has('metaLine') ? 'is-shown' : ''}`}
          style={{
            display: 'none',
          }}
        />
      </div>

      {/* ───────── TREND STRIP · period-based trends with sparklines + deltas ───────── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: 12,
          marginBottom: 16,
        }}
      >
        {[
          { id: 'trend1', label: '每日仓库', value: summary?.by_period?.daily ?? 0, series: dailySeries, color: 'var(--accent)', delta: dailyDelta },
          { id: 'trend2', label: '每周仓库', value: summary?.by_period?.weekly ?? 0, series: weeklySeries, color: 'var(--sage)', delta: weeklyDelta },
          { id: 'trend3', label: '每月仓库', value: summary?.by_period?.monthly ?? 0, series: monthlySeries, color: 'var(--ochre)', delta: monthlyDelta },
          { id: 'trend4', label: '累计趋势', value: summary?.total_repos ?? 0, series: totalSeries, color: 'var(--plum)', delta: totalDelta },
        ].map((tile) => (
          <div
            key={tile.label}
            className={`scatter-item ${shown.has(tile.id) ? 'is-shown' : ''}`}
            style={{
              padding: '14px 16px',
              background: 'rgba(252, 250, 254, 0.97)',
              backdropFilter: 'blur(12px) saturate(140%)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
              transition: 'box-shadow 200ms ease, border-color 200ms ease',
            }}
          >
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="metric-label" style={{ marginBottom: 4 }}>{tile.label}</div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                <div
                  className="display"
                  style={{
                    fontSize: 24,
                    fontWeight: 700,
                    lineHeight: 1,
                    letterSpacing: '-0.02em',
                    fontVariantNumeric: 'tabular-nums',
                    color: tile.color,
                  }}
                >
                  {Number(tile.value).toLocaleString()}
                </div>
                {/* day-over-day delta badge */}
                {tile.delta && (
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      fontFamily: 'var(--font-mono)',
                      fontVariantNumeric: 'tabular-nums',
                      color: tile.delta.delta >= 0 ? 'var(--sage-soft)' : 'var(--coral-soft)',
                      background: tile.delta.delta >= 0 ? 'rgba(47, 158, 68, 0.08)' : 'rgba(214, 51, 108, 0.08)',
                      padding: '1px 6px',
                      borderRadius: 4,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {tile.delta.delta >= 0 ? '↑' : '↓'} {tile.delta.pct}
                  </span>
                )}
              </div>
            </div>
            {tile.series.length > 0 && (
              <Sparkline data={tile.series} width={72} height={32} color={tile.color} />
            )}
          </div>
        ))}
      </div>

      {/* ───────── CHARTS ───────── */}
      <div className={`scatter-item ${shown.has('charts') ? 'is-shown' : ''}`}>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card title="语言分布 · Top 15" style={{ marginBottom: 0, height: 280 }}>
            <div style={{ display: 'flex', alignItems: 'center', height: 190, gap: 16 }}>
              {/* Doughnut chart — center-left */}
              <div style={{ flex: '0 0 45%', height: '100%' }}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={langChartData}
                      dataKey="count"
                      nameKey="language"
                      cx="50%"
                      cy="50%"
                      outerRadius={68}
                      innerRadius={40}
                      stroke="rgba(255, 255, 255, 0.8)"
                      animationBegin={200}
                      animationDuration={900}
                    >
                      {langChartData.map((_, i) => (
                        <Cell key={i} fill={langLegend[i].color} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        background: 'rgba(255, 255, 255, 0.95)',
                        border: '1px solid var(--border-strong)',
                        borderRadius: '10px',
                        fontFamily: 'var(--font-mono)',
                        fontSize: 12,
                        color: 'var(--text)',
                        boxShadow: 'var(--shadow-md)',
                      }}
                      labelStyle={{ color: 'var(--text-muted)' }}
                      itemStyle={{ color: 'var(--text)' }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              {/* 2-column legend — 15 entries fit in a compact grid without
                  increasing card height. overflow-y auto is a safety net. */}
              <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '3px 12px', fontSize: 11, overflowY: 'auto', paddingRight: 4, alignContent: 'center' }}>
                {langLegend.map((entry) => (
                  <div key={entry.name} style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: entry.color, flexShrink: 0 }} />
                    <span style={{ color: 'var(--text)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{entry.name}</span>
                    <span style={{ color: 'var(--text-dim)', fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>{entry.percent}%</span>
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </Col>
        <Col span={12}>
          <Card title="近 30 天 · 抓取时间线" style={{ marginBottom: 0, height: 280 }}>
            <ResponsiveContainer width="100%" height={190}>
              <BarChart data={timelineFull}>
                <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" />
                <XAxis
                  dataKey="date"
                  stroke="var(--text-dim)"
                  tick={{ fontFamily: 'var(--font-mono)', fontSize: 10 }}
                  tickFormatter={(d: string) => d.slice(5)}
                  interval="preserveStartEnd"
                  minTickGap={20}
                />
                <YAxis stroke="var(--text-dim)" tick={{ fontFamily: 'var(--font-mono)', fontSize: 10 }} />
                <Tooltip
                  contentStyle={{
                    background: 'rgba(255, 255, 255, 0.95)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: '10px',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 12,
                    color: 'var(--text)',
                    boxShadow: 'var(--shadow-md)',
                  }}
                  labelStyle={{ color: 'var(--text-muted)' }}
                  itemStyle={{ color: 'var(--text)' }}
                  cursor={{ fill: 'rgba(66, 99, 235, 0.06)' }}
                />
                <Legend
                  verticalAlign="bottom"
                  align="center"
                  formatter={(v) => <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-sans)', fontSize: 12 }}>{v}</span>}
                />
                <Bar dataKey="daily" fill="#a5d8ff" name="Daily" radius={[3, 3, 0, 0]} animationBegin={200} animationDuration={900} />
                <Bar dataKey="weekly" fill="#b2f2bb" name="Weekly" radius={[3, 3, 0, 0]} animationBegin={400} animationDuration={900} />
                <Bar dataKey="monthly" fill="#ffd8a8" name="Monthly" radius={[3, 3, 0, 0]} animationBegin={600} animationDuration={900} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>
      </div>

      {/* ───────── TRENDING ───────── */}
      <div className={`scatter-item ${shown.has('trending') ? 'is-shown' : ''}`}>
      <Card
        title={
          <Space>
            <RiseOutlined style={{ color: 'var(--accent)' }} />
            <span>Trending · Top 10</span>
          </Space>
        }
        extra={
          <Segmented
            size="small"
            value={trendPeriod}
            onChange={(v) => setTrendPeriod(v as 'daily' | 'weekly' | 'monthly')}
            options={[
              { label: '每日', value: 'daily' },
              { label: '每周', value: 'weekly' },
              { label: '每月', value: 'monthly' },
            ]}
          />
        }
      >
        <div className={`list-fade ${trendLoading && topRepos.length > 0 ? 'list-fade-out' : ''}`}>
        <Table
          rowKey={(r) => r.repo.id}
          dataSource={topRepos}
          pagination={false}
          size="small"
          columns={[
            {
              title: '#',
              width: 56,
              render: (_, __, idx) => (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 28,
                    height: 28,
                    borderRadius: '50%',
                    background: idx < 3 ? `${rankColors[idx]}22` : 'transparent',
                    border: idx < 3 ? `1px solid ${rankColors[idx]}` : '1px solid var(--border)',
                    color: idx < 3 ? rankColors[idx] : 'var(--text-dim)',
                    fontSize: 12,
                    fontWeight: 700,
                    fontFamily: 'var(--font-mono)',
                    boxShadow: idx < 3 ? `0 2px 8px ${rankColors[idx]}33` : 'none',
                  }}
                >
                  {String(idx + 1).padStart(2, '0')}
                </div>
              ),
            },
            {
              title: '仓库',
              dataIndex: ['repo', 'full_name'],
              render: (name: string, record: TopRepo) => (
                <div>
                  <Link to={`/repos/${record.repo.id}`} style={{ fontWeight: 500, borderBottom: 'none' }}>
                    {name}
                  </Link>
                  <div className="repo-desc">{record.repo.description || '—'}</div>
                </div>
              ),
            },
            {
              title: 'AI 中文解读',
              width: 280,
              dataIndex: ['repo', 'latest_interpretation'],
              render: (_: any, record: TopRepo) => {
                const interp = record.repo.latest_interpretation;
                if (!interp || !interp.summary_cn) {
                  return <Text type="secondary" style={{ fontSize: 12 }}>—</Text>;
                }
                return (
                  <AntTooltip
                    title={
                      <div style={{ whiteSpace: 'pre-wrap' }}>
                        {interp.value_prop || ''}
                        {interp.value_prop ? '\n' : ''}
                        {`难度: ${interp.difficulty || '—'} / 5  学习: ${interp.learning_hours || '—'} 小时`}
                      </div>
                    }
                    placement="topLeft"
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 4 }}>
                      <RobotOutlined style={{ color: 'var(--sage-soft)', marginTop: 3, fontSize: 13, flexShrink: 0 }} />
                      <Paragraph
                        style={{ margin: 0, fontSize: 12, color: 'var(--text)', lineHeight: 1.45 }}
                        ellipsis={{ rows: 2, tooltip: false }}
                      >
                        {interp.summary_cn}
                      </Paragraph>
                    </div>
                  </AntTooltip>
                );
              },
            },
            {
              title: '语言',
              width: 100,
              dataIndex: ['repo', 'language'],
              render: (lang: string | null) =>
                lang ? <Tag color="blue">{lang}</Tag> : <Text type="secondary">—</Text>,
            },
            {
              title: '新增 ★',
              width: 110,
              dataIndex: 'metric_value',
              sorter: (a: TopRepo, b: TopRepo) => a.metric_value - b.metric_value,
              render: (v: number, record: TopRepo) => (
                <span
                  className="display"
                  style={{
                    fontWeight: 700,
                    color: 'var(--coral-soft)',
                    fontSize: 18,
                    fontVariantNumeric: 'tabular-nums',
                    textShadow: '0 0 12px rgba(240, 136, 62, 0.4)',
                  }}
                >
                  {record.is_estimated && (
                    <AntTooltip title="首次抓取无历史快照, 增量为估算值, 系统运行满一个周期后自动转精确">
                      <span style={{ fontSize: 11, color: 'var(--text-dim)', marginRight: 3, fontWeight: 400 }}>约</span>
                    </AntTooltip>
                  )}
                  +{v.toLocaleString()}
                </span>
              ),
            },
            {
              title: '总 ★',
              width: 90,
              dataIndex: ['repo', 'stargazers_count'],
              render: (v: number) => (
                <span className="mono numeric" style={{ color: 'var(--ochre-soft)', fontWeight: 600 }}>
                  ★ {v.toLocaleString()}
                </span>
              ),
            },
            {
              title: 'Forks',
              width: 75,
              dataIndex: ['repo', 'forks_count'],
              render: (v: number) => <span className="mono numeric">{v.toLocaleString()}</span>,
            },
          ]}
        />
        </div>
      </Card>
      </div>
    </div>
  );
}
