import { useEffect, useRef, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { RobotOutlined, StarFilled, StarOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { SortOrder } from 'antd/es/table/interface';
import { Link, useSearchParams } from 'react-router-dom';
import { formatCompact } from '../utils';
import { getRepos, getLanguages, getFavorites, toggleFavorite, triggerInterpretSync } from '../api/client';
import type { LanguageStat, Period, Repository, RepositoryList, SortField } from '../types';
import Sparkline from '../components/Sparkline';
import { useScatterReveal } from '../hooks/useScatterReveal';
import { useDebouncedInput } from '../hooks/useDebouncedInput';
import { useInterpAuthed } from '../hooks/useInterpAuthed';

const { Text, Paragraph } = Typography;

const sortOptions: { value: SortField; label: string }[] = [
  { value: 'gained', label: '新增 Star' },
  { value: 'stars', label: '总 Star 数' },
  { value: 'forks', label: 'Fork 数' },
  { value: 'updated', label: '更新时间' },
  { value: 'created', label: '创建时间' },
  { value: 'pushed', label: 'Push 时间' },
  { value: 'name', label: '名称' },
];

const regionOptions = [
  { value: '', label: '全部地区' },
  { value: 'china', label: '🇨🇳 中国' },
  { value: 'overseas', label: '🌍 海外' },
];

const categoryOptions = [
  { value: '', label: '全部分类' },
  { value: 'ai', label: 'AI/机器学习' },
  { value: 'frontend', label: '前端' },
  { value: 'backend', label: '后端' },
  { value: 'devops', label: '运维/DevOps' },
  { value: 'security', label: '安全' },
  { value: 'database', label: '数据库' },
  { value: 'mobile', label: '移动开发' },
  { value: 'game', label: '游戏开发' },
  { value: 'data', label: '数据科学' },
  { value: 'blockchain', label: '区块链' },
  { value: 'iot', label: '物联网' },
];

const categoryLabels: Record<string, string> = {
  ai: 'AI', frontend: '前端', backend: '后端', devops: '运维',
  security: '安全', database: '数据库', mobile: '移动', game: '游戏',
  data: '数据', blockchain: '区块链', iot: '物联网',
};

interface ReposPageProps {
  initialRegion?: 'china' | 'overseas' | 'unknown';
  pageTitle?: string;
}

/* ── module-level list cache: survives route changes within a session so the
   list doesn't refetch when returning from a detail page. Keyed by the full
   search-param string. ── */
interface ListCache {
  key: string;
  data: RepositoryList;
  languages: LanguageStat[];
  ts: number;
}
let listCache: ListCache | null = null;
const CACHE_TTL = 90_000; // 90s

export default function ReposPage(props: ReposPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<RepositoryList | null>(null);
  const [languages, setLanguages] = useState<LanguageStat[]>([]);
  const [loading, setLoading] = useState(false);
  const [favSet, setFavSet] = useState<Set<number>>(new Set());
  const [showFilters, setShowFilters] = useState(false);
  const [onlyFav, setOnlyFav] = useState(false);
  const [interpLoadingIds, setInterpLoadingIds] = useState<Set<number>>(new Set());
  const firstRun = useRef(true);
  // 分页切换后, 等数据加载完成再滚动到顶部 (避免在 loading 中途滚动)
  const prevDataPage = useRef<number | null>(null);
  // entrance animation — random scatter reveal of toolbar / filters / trend / table
  const { shown: shownReveal } = useScatterReveal(
    ['toolbar', 'filters', 'trendStrip', 'table'],
    200,
  );
  const interpAuthed = useInterpAuthed();

  // 从 URL 同步筛选条件. 默认 period=daily, sort=gained
  const filter = {
    period: (searchParams.get('period') as Period) || 'daily',
    language: searchParams.get('language') || undefined,
    min_stars: searchParams.get('min_stars')
      ? Number(searchParams.get('min_stars'))
      : undefined,
    max_stars: searchParams.get('max_stars')
      ? Number(searchParams.get('max_stars'))
      : undefined,
    topic: searchParams.get('topic') || undefined,
    license: searchParams.get('license') || undefined,
    q: searchParams.get('q') || undefined,
    sort: (searchParams.get('sort') as SortField) || 'gained',
    order: (searchParams.get('order') as 'asc' | 'desc') || 'desc',
    page: Number(searchParams.get('page')) || 1,
    per_page: Number(searchParams.get('per_page')) || 20,
    region: (searchParams.get('region') as 'china' | 'overseas' | undefined) || props.initialRegion,
    category: searchParams.get('category') || undefined,
    is_chinese_doc: searchParams.get('is_chinese_doc') === '1' ? true : undefined,
    is_efficiency_tool: searchParams.get('is_efficiency_tool') === '1' ? true : undefined,
    industry: searchParams.get('industry') || undefined,
  };

  async function load(silent = false) {
    if (!silent) setLoading(true);
    try {
      const params: Record<string, unknown> = {};
      Object.entries(filter).forEach(([k, v]) => {
        // period=all 是有效值, 需要传给后端; 只过滤 undefined 和空字符串
        if (v !== undefined && v !== '') params[k] = v;
      });
      // "仅看收藏": 把本地收藏 ID 列表传给后端, 由后端按 id 列表过滤 + 分页,
      // 避免只筛当前页导致其他页的收藏看不到. 无收藏时传 "-1" 让后端返回空集.
      if (onlyFav) {
        const favs = getFavorites();
        params.favorite_ids = favs.length > 0 ? favs.join(',') : '-1';
      }
      const [repos, langs] = await Promise.all([
        getRepos(params as any),
        getLanguages(50),
      ]);
      setData(repos);
      setLanguages(langs);
      setFavSet(new Set(JSON.parse(localStorage.getItem('find-github-favorites') || '[]')));
      // persist to cache so returning from a detail page is instant
      listCache = {
        key: searchParams.toString(),
        data: repos,
        languages: langs,
        ts: Date.now(),
      };
    } catch (e) {
      if (!silent) message.error('加载失败: ' + (e as Error).message);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  // cache key for the current filter set
  const cacheKey = searchParams.toString();

  useEffect(() => {
    // first run of this mount: try cache restoration before fetching
    if (firstRun.current) {
      firstRun.current = false;
      const hit = listCache && listCache.key === cacheKey && Date.now() - listCache.ts < CACHE_TTL;
      if (hit && listCache) {
        setData(listCache.data);
        setLanguages(listCache.languages);
        setLoading(false);
        // silent background refresh — keeps data fresh
        load(true);
        return;
      }
    }
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    cacheKey,
    onlyFav,
    filter.period,
    filter.language,
    filter.min_stars,
    filter.max_stars,
    filter.topic,
    filter.license,
    filter.q,
    filter.sort,
    filter.order,
    filter.page,
    filter.per_page,
    filter.region,
    filter.category,
    filter.is_chinese_doc,
    filter.is_efficiency_tool,
    filter.industry,
  ]);

  // 分页切换后, 等数据加载完成 (data.page 更新) 再滚动到顶部.
  // 跳过首次加载 (null → page=1), 避免页面挂载时的额外滚动.
  useEffect(() => {
    if (!data) return;
    if (prevDataPage.current === null) {
      prevDataPage.current = data.page;
      return;
    }
    if (prevDataPage.current !== data.page) {
      prevDataPage.current = data.page;
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }, [data?.page]);

  function updateFilter(patch: Record<string, string | number | undefined>) {
    const next = new URLSearchParams(searchParams);
    Object.entries(patch).forEach(([k, v]) => {
      // 只删除 undefined 和空字符串; 'all' 是 period 的有效值, 需要保留
      if (v === undefined || v === '') {
        next.delete(k);
      } else {
        next.set(k, String(v));
      }
    });
    if (!('page' in patch)) next.delete('page');
    setSearchParams(next);
  }

  // 防抖输入: 本地即时显示, 400ms 后提交到 URL filter 触发搜索
  const [qInput, setQInput] = useDebouncedInput(
    filter.q ?? '',
    (v) => updateFilter({ q: v || undefined }),
  );
  const [topicInput, setTopicInput] = useDebouncedInput(
    filter.topic ?? '',
    (v) => updateFilter({ topic: v || undefined }),
  );
  const [licenseInput, setLicenseInput] = useDebouncedInput(
    filter.license ?? '',
    (v) => updateFilter({ license: v || undefined }),
  );
  const [minStarsInput, setMinStarsInput] = useDebouncedInput(
    filter.min_stars,
    (v) => updateFilter({ min_stars: v ?? undefined }),
  );
  const [maxStarsInput, setMaxStarsInput] = useDebouncedInput(
    filter.max_stars,
    (v) => updateFilter({ max_stars: v ?? undefined }),
  );

  function handleReset() {
    const next = new URLSearchParams();
    if (props.initialRegion) next.set('region', props.initialRegion);
    setSearchParams(next);
  }

  function handleToggleFav(id: number) {
    const added = toggleFavorite(id);
    setFavSet(new Set(JSON.parse(localStorage.getItem('find-github-favorites') || '[]')));
    message.success(added ? '已收藏' : '已取消收藏');
  }

  async function handleGenerateInterp(repoId: number) {
    if (!interpAuthed) return;
    setInterpLoadingIds((prev) => new Set(prev).add(repoId));
    try {
      const result = await triggerInterpretSync([repoId], 1, true);
      if (result.success > 0) {
        message.success('AI 解读已生成');
        await load();
      } else {
        message.warning('AI 解读未生成, 请检查后端 LLM 配置');
      }
    } catch (e) {
      message.error('生成失败: ' + (e as Error).message);
    } finally {
      setInterpLoadingIds((prev) => {
        const next = new Set(prev);
        next.delete(repoId);
        return next;
      });
    }
  }

  const languageOptions = [
    { value: '', label: '全部语言' },
    ...languages
      .filter((l) => l.language)
      .map((l) => ({ value: l.language!, label: `${l.language} (${l.count})` })),
  ];

  const hasPeriod = filter.period && filter.period !== 'all';

  // 收藏筛选已下沉到后端 (favorite_ids), 这里直接使用后端返回的当前页数据
  const displayItems = data?.items ?? [];

  // 完全受控排序: 同列点击切换方向, 不同列默认降序
  function handleTableChange(pagination: any, _filters: any, sorter: any) {
    if (sorter && sorter.field) {
      const fieldToSort: Record<string, SortField> = {
        stars_gained: 'gained',
        stargazers_count: 'stars',
        forks_count: 'forks',
        updated_at: 'updated',
        created_at: 'created',
        pushed_at: 'pushed',
        name: 'name',
      };
      const sortField = fieldToSort[sorter.field] || 'stars';
      let order: 'asc' | 'desc';
      if (filter.sort === sortField) {
        order = filter.order === 'desc' ? 'asc' : 'desc';
      } else {
        order = 'desc';
      }
      updateFilter({ sort: sortField, order });
    }
    if (pagination.current && pagination.current !== filter.page) {
      updateFilter({ page: pagination.current });
    }
    if (pagination.pageSize && pagination.pageSize !== filter.per_page) {
      updateFilter({ page: 1, per_page: pagination.pageSize });
    }
  }

  const maxGained = Math.max(
    1,
    ...(data?.items ?? []).map((r) => r.stars_gained ?? 0),
  );

  // aggregate trend metrics: numeric totals from backend aggregates (跨所有分页),
  // sparkline series from current page (展示分布形态)
  const items = data?.items ?? [];
  const agg = data?.aggregates;
  const totalStars = agg?.total_stars ?? items.reduce((s, r) => s + (r.stargazers_count ?? 0), 0);
  const totalGained = agg?.total_gained ?? items.reduce((s, r) => s + (r.stars_gained ?? 0), 0);
  const totalForks = agg?.total_forks ?? items.reduce((s, r) => s + (r.forks_count ?? 0), 0);
  const chineseCount = agg?.chinese_count ?? items.filter((r) => r.is_chinese_owner).length;
  const interpretedCount = agg?.interpreted_count ?? items.filter((r) => r.latest_interpretation?.summary_cn).length;
  // sparkline series: top 12 repos' stars_gained (sorted desc) — shows distribution shape
  const gainedSeries = items
    .map((r) => r.stars_gained ?? 0)
    .sort((a, b) => b - a)
    .slice(0, 12);
  const starsSeries = items
    .map((r) => r.stargazers_count ?? 0)
    .sort((a, b) => b - a)
    .slice(0, 12);
  const forksSeries = items
    .map((r) => r.forks_count ?? 0)
    .sort((a, b) => b - a)
    .slice(0, 12);

  const columns: ColumnsType<Repository> = [
    {
      title: '仓库',
      dataIndex: 'full_name',
      ellipsis: true,
      render: (name: string, record: Repository) => (
        <div className="row-accent" style={{ paddingLeft: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
            <Link to={`/repos/${record.id}`} target="_blank" style={{ fontWeight: 500, borderBottom: 'none' }}>
              {name}
            </Link>
            {favSet.has(record.id) && <Tag color="gold" style={{ marginRight: 0 }}>★</Tag>}
            {record.is_chinese_owner && <Tag color="red" style={{ marginRight: 0 }}>国产</Tag>}
            {record.has_chinese_doc && <Tag color="green" style={{ marginRight: 0 }}>中文</Tag>}
            {record.is_efficiency_tool && <Tag color="purple" style={{ marginRight: 0 }}>工具</Tag>}
          </div>
          <div className="repo-desc">{record.description || '—'}</div>
        </div>
      ),
    },
    {
      title: 'AI 中文解读',
      width: 260,
      dataIndex: 'latest_interpretation',
      render: (_: any, record: Repository) => {
        const interp = record.latest_interpretation;
        if (!interp || !interp.summary_cn) {
          if (!interpAuthed) return <Text type="secondary">—</Text>;
          return (
            <Tooltip title="点击图标同步生成 AI 解读 (约 5-15 秒)">
              <Button
                type="text"
                size="small"
                icon={<RobotOutlined />}
                loading={interpLoadingIds.has(record.id)}
                onClick={() => handleGenerateInterp(record.id)}
              >
                生成解读
              </Button>
            </Tooltip>
          );
        }
        return (
          <Tooltip
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
              <RobotOutlined style={{ color: 'var(--slate)', marginTop: 4, fontSize: 14, flexShrink: 0 }} />
              <Paragraph
                style={{ margin: 0, fontSize: 13, color: 'var(--text)' }}
                ellipsis={{ rows: 2, tooltip: false }}
              >
                {interp.summary_cn}
              </Paragraph>
            </div>
          </Tooltip>
        );
      },
    },
    {
      title: '分类',
      width: 70,
      dataIndex: 'category',
      render: (cat: string | null) => {
        if (!cat) return <Text type="secondary">—</Text>;
        return <Tag color="cyan">{categoryLabels[cat] || cat}</Tag>;
      },
    },
    {
      title: '语言',
      width: 80,
      dataIndex: 'language',
      render: (lang: string | null) =>
        lang ? <Tag color="blue">{lang}</Tag> : <Text type="secondary">—</Text>,
    },
    ...(hasPeriod
      ? [
          {
            title: `新增 ★`,
            width: 110,
            dataIndex: 'stars_gained' as keyof Repository,
            sorter: true,
            sortDirections: ['descend', 'ascend'] as SortOrder[],
            sortOrder:
              filter.sort === 'gained'
                ? filter.order === 'desc'
                  ? ('descend' as const)
                  : ('ascend' as const)
                : undefined,
            render: (v: number | null, record: Repository) => {
              if (v == null || v <= 0) return <Text type="secondary">—</Text>;
              const ratio = Math.min(1, v / maxGained);
              const isHot = v >= maxGained * 0.7;
              return (
                <div>
                  <span
                    className={`numeric rise-glow ${isHot ? 'glow-num' : ''}`}
                    style={{
                      fontWeight: 700,
                      color: isHot ? 'var(--coral-soft)' : 'var(--accent-bright)',
                      fontSize: 15,
                      display: 'inline-block',
                    }}
                  >
                    +{v.toLocaleString()}
                  </span>
                  {isHot && <span className="hot-badge" style={{ marginLeft: 6, padding: '1px 5px', fontSize: 9 }}>HOT</span>}
                  <div className="mini-bar" style={{ marginTop: 4 }}>
                    <span style={{ ['--bar-w' as any]: `${ratio * 100}%` }} />
                  </div>
                </div>
              );
            },
          },
        ]
      : []),
    {
      title: '★ Star',
      width: 100,
      dataIndex: 'stargazers_count',
      sorter: true,
      sortDirections: ['descend', 'ascend'] as SortOrder[],
      sortOrder:
        filter.sort === 'stars'
          ? filter.order === 'desc'
            ? ('descend' as const)
            : ('ascend' as const)
          : undefined,
      render: (v: number) => (
        <span className="mono numeric" style={{ fontWeight: 600, color: 'var(--ochre-soft)', textShadow: '0 0 8px var(--ochre-glow)' }}>
          ★ {v.toLocaleString()}
        </span>
      ),
    },
    {
      title: '收藏',
      width: 50,
      align: 'center' as const,
      render: (_: any, record: Repository) => (
        <Button
          type="text"
          size="small"
          icon={
            favSet.has(record.id) ? (
              <StarFilled style={{ color: 'var(--ochre)' }} />
            ) : (
              <StarOutlined />
            )
          }
          onClick={() => handleToggleFav(record.id)}
        />
      ),
    },
    {
      title: 'GH',
      width: 50,
      align: 'center' as const,
      render: (_: any, record: Repository) => (
        <a href={record.html_url} target="_blank" rel="noreferrer" className="mono" style={{ borderBottom: 'none' }}>
          ↗
        </a>
      ),
    },
  ];

  return (
    <div className="page-shell anim-fade-up">
      {/* compact toolbar — period switcher + live status */}
      <div className={`page-toolbar scatter-item ${shownReveal.has('toolbar') ? 'is-shown' : ''}`}>
        <div className="page-toolbar-left">
          <span className="signal-chip">
            <span className="dot" />
            LIVE
          </span>
          <span className="page-toolbar-meta">
            {data?.total ?? 0} entries · {filter.period === 'all' ? '聚合视图' : `${filter.period.toUpperCase()} 窗口`}
          </span>
        </div>
        <Segmented
          value={filter.period}
          onChange={(v) => {
            const newPeriod = v as Period;
            const oldPeriod = filter.period;
            if (newPeriod === 'all' && filter.sort === 'gained') {
              // 切到"全部": period=all 无 stars_gained, sort=gained 自动切换为 stars
              updateFilter({ period: newPeriod, sort: 'stars' });
            } else if (
              oldPeriod === 'all' &&
              newPeriod !== 'all' &&
              filter.sort === 'stars'
            ) {
              // 从"全部"切回 daily/weekly/monthly: 恢复默认 sort=gained (新增 Star)
              // 仅当 sort 仍是 all 下的默认值 stars 时恢复, 避免覆盖用户手动选择的其他排序
              updateFilter({ period: newPeriod, sort: 'gained' });
            } else {
              updateFilter({ period: newPeriod });
            }
          }}
          options={[
            { label: '全部', value: 'all' },
            { label: '每日', value: 'daily' },
            { label: '每周', value: 'weekly' },
            { label: '每月', value: 'monthly' },
          ]}
        />
      </div>

      {/* compact filter row */}
      <Card size="small" className={`scatter-item ${shownReveal.has('filters') ? 'is-shown' : ''}`} style={{ marginBottom: 12, flexShrink: 0 }}>
        <Row gutter={[12, 8]} align="middle">
          <Col>
            <Space>
              <Text type="secondary" className="label-mono">排序</Text>
              <Select
                size="small"
                value={filter.sort}
                options={sortOptions}
                onChange={(v) => updateFilter({ sort: v })}
                style={{ width: 130 }}
              />
              <Select
                size="small"
                value={filter.order}
                options={[
                  { value: 'desc', label: '降序' },
                  { value: 'asc', label: '升序' },
                ]}
                onChange={(v) => updateFilter({ order: v })}
                style={{ width: 80 }}
              />
            </Space>
          </Col>
          <Col>
            <Space>
              <Text type="secondary" className="label-mono">语言</Text>
              <Select
                size="small"
                value={filter.language || ''}
                options={languageOptions}
                onChange={(v) => updateFilter({ language: v || undefined })}
                showSearch
                placeholder="全部语言"
                style={{ width: 150 }}
              />
            </Space>
          </Col>
          <Col>
            <Space>
              <Text type="secondary" className="label-mono">分类</Text>
              <Select
                size="small"
                value={filter.category || ''}
                options={categoryOptions}
                onChange={(v) => updateFilter({ category: v || undefined })}
                placeholder="全部分类"
                style={{ width: 130 }}
              />
            </Space>
          </Col>
          <Col>
            <Space>
              <Text type="secondary" className="label-mono">地区</Text>
              <Select
                size="small"
                value={filter.region || ''}
                options={regionOptions}
                onChange={(v) => updateFilter({ region: v || undefined })}
                placeholder="全部地区"
                style={{ width: 110 }}
              />
            </Space>
          </Col>
          <Col>
            <Space>
              <Text type="secondary" className="label-mono">仅看收藏</Text>
              <Switch
                size="small"
                checked={onlyFav}
                onChange={(checked) => {
                  setOnlyFav(checked);
                  // 切换收藏筛选后回到第 1 页, 避免停留在不存在的页码
                  updateFilter({ page: 1 });
                }}
              />
            </Space>
          </Col>
          <Col flex="auto">
            <Input
              size="small"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="搜索仓库名/描述"
              allowClear
              style={{ maxWidth: 220 }}
            />
          </Col>
          <Col>
            <Space>
              <Button size="small" onClick={() => setShowFilters((v) => !v)}>
                {showFilters ? '收起' : '更多筛选'}
              </Button>
              <Button size="small" onClick={handleReset}>重置</Button>
              <Text type="secondary" className="mono" style={{ fontSize: 11 }}>
                {data?.total ?? 0} 条
              </Text>
            </Space>
          </Col>
        </Row>

        {/* advanced filters */}
        {showFilters && (
          <Row gutter={[12, 8]} style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            <Col span={4}>
              <Form.Item label="Min★" style={{ marginBottom: 0 }}>
                <InputNumber
                  size="small"
                  value={minStarsInput}
                  onChange={(v) => setMinStarsInput(v ?? undefined)}
                  placeholder="0"
                  style={{ width: '100%' }}
                  min={0}
                />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item label="Max★" style={{ marginBottom: 0 }}>
                <InputNumber
                  size="small"
                  value={maxStarsInput}
                  onChange={(v) => setMaxStarsInput(v ?? undefined)}
                  placeholder="不限"
                  style={{ width: '100%' }}
                  min={0}
                />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item label="主题" style={{ marginBottom: 0 }}>
                <Input
                  size="small"
                  value={topicInput}
                  onChange={(e) => setTopicInput(e.target.value)}
                  placeholder="如: ai"
                  allowClear
                />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item label="许可证" style={{ marginBottom: 0 }}>
                <Input
                  size="small"
                  value={licenseInput}
                  onChange={(e) => setLicenseInput(e.target.value)}
                  placeholder="如: MIT"
                  allowClear
                />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item label="中文文档" style={{ marginBottom: 0 }}>
                <Select
                  size="small"
                  value={filter.is_chinese_doc === true ? '1' : ''}
                  options={[
                    { value: '', label: '不限' },
                    { value: '1', label: '仅中文友好' },
                  ]}
                  onChange={(v) => updateFilter({ is_chinese_doc: v === '1' ? '1' : undefined })}
                  style={{ width: '100%' }}
                />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item label="效率工具" style={{ marginBottom: 0 }}>
                <Select
                  size="small"
                  value={filter.is_efficiency_tool === true ? '1' : ''}
                  options={[
                    { value: '', label: '不限' },
                    { value: '1', label: '仅效率工具' },
                  ]}
                  onChange={(v) => updateFilter({ is_efficiency_tool: v === '1' ? '1' : undefined })}
                  style={{ width: '100%' }}
                />
              </Form.Item>
            </Col>
          </Row>
        )}
      </Card>

      {/* trend overview strip — aggregate metrics (跨所有分页) + sparklines (本页分布) */}
      {items.length > 0 && (
        <div
          className={`scatter-item ${shownReveal.has('trendStrip') ? 'is-shown' : ''}`}
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 10,
            marginBottom: 12,
            flexShrink: 0,
          }}
        >
          {[
            { label: '总 Star', value: totalStars, series: starsSeries, color: 'var(--ochre)' },
            { label: hasPeriod ? '新增 ★' : '总 Star', value: hasPeriod ? totalGained : totalStars, series: gainedSeries, color: 'var(--coral)' },
            { label: '总 Forks', value: totalForks, series: forksSeries, color: 'var(--accent)' },
            { label: '国产仓库', value: chineseCount, series: [], color: 'var(--plum)' },
            { label: '已解读', value: interpretedCount, series: [], color: 'var(--sage)' },
            { label: '本页条数', value: items.length, series: [], color: 'var(--text-muted)' },
          ].map((tile) => (
            <div
              key={tile.label}
              style={{
                padding: '10px 12px',
                background: 'rgba(252, 250, 254, 0.97)',
                backdropFilter: 'blur(10px)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 8,
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div className="metric-label" style={{ marginBottom: 2, fontSize: 9 }}>{tile.label}</div>
                <div
                  className="display"
                  style={{
                    fontSize: 18,
                    fontWeight: 700,
                    lineHeight: 1,
                    fontVariantNumeric: 'tabular-nums',
                    color: tile.color,
                  }}
                >
                  {formatCompact(Number(tile.value))}
                </div>
              </div>
              {tile.series.length > 0 && (
                <Sparkline data={tile.series} width={56} height={24} color={tile.color} />
              )}
            </div>
          ))}
        </div>
      )}

      <Card bodyStyle={{ padding: 0 }} className={`scatter-item ${shownReveal.has('table') ? 'is-shown' : ''}`} style={{ flex: 1, minHeight: 0 }}>
        <div className="list-fade">
        <Table<Repository>
          rowKey="id"
          loading={loading}
          dataSource={displayItems}
          size="small"
          tableLayout="fixed"
          pagination={{
            current: data?.page ?? 1,
            pageSize: data?.per_page ?? 20,
            total: data?.total ?? 0,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            showTotal: (t) => `共 ${t} 条`,
            showQuickJumper: true,
            size: 'small',
            onChange: (page, pageSize) => {
              updateFilter({ page, per_page: pageSize });
            },
          }}
          onChange={handleTableChange}
          columns={columns}
        />
        </div>
      </Card>
    </div>
  );
}
