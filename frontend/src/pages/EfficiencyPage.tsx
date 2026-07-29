import { useEffect, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Row,
  Segmented,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { RobotOutlined, StarFilled, StarOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { Link } from 'react-router-dom';
import { getRepos, getIndustryStats, toggleFavorite, triggerInterpretSync } from '../api/client';
import type { IndustryStat, Period, Repository, RepositoryList } from '../types';
import { useScatterReveal } from '../hooks/useScatterReveal';
import { useInterpAuthed } from '../hooks/useInterpAuthed';

const { Text, Paragraph } = Typography;

const INDUSTRY_LABEL: Record<string, string> = {
  developer: '开发者工具',
  design: '设计工具',
  pm: '项目管理',
  writing: '写作/笔记',
  data: '数据分析',
  office: '办公自动化',
  'ai-assistant': 'AI助手',
  marketing: '营销运营',
  operation: '运营自动化',
  finance: '财务',
  education: '教育学习',
  media: '音视频',
};

const INDUSTRY_KEYS = Object.keys(INDUSTRY_LABEL);

function industryLabel(industry: string | null): string {
  if (!industry) return '其他';
  return INDUSTRY_LABEL[industry] || industry;
}

const ALL_KEY = 'all';

const segmentOptions = [
  { value: ALL_KEY, label: '全部' },
  ...INDUSTRY_KEYS.map((k) => ({ value: k, label: INDUSTRY_LABEL[k] })),
];

export default function EfficiencyPage() {
  const [stats, setStats] = useState<IndustryStat[]>([]);
  const [data, setData] = useState<RepositoryList | null>(null);
  const [loadingStats, setLoadingStats] = useState(false);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [active, setActive] = useState<string>(ALL_KEY);
  const [favSet, setFavSet] = useState<Set<number>>(new Set());
  const [period, setPeriod] = useState<Period>('all');
  const [interpLoadingIds, setInterpLoadingIds] = useState<Set<number>>(new Set());
  // entrance animation — random scatter reveal of toolbar / industry cards / table
  const { shown: shownReveal } = useScatterReveal(
    ['toolbar', 'industryCards', 'table'],
    200,
  );
  const interpAuthed = useInterpAuthed();

  async function loadStats(periodValue: Period) {
    setLoadingStats(true);
    try {
      const s = await getIndustryStats(periodValue);
      setStats(s);
    } catch (e) {
      message.error('加载行业统计失败: ' + (e as Error).message);
    } finally {
      setLoadingStats(false);
    }
  }

  async function loadRepos(industryKey: string, periodValue: Period) {
    setLoadingRepos(true);
    try {
      const res = await getRepos({
        is_efficiency_tool: true,
        per_page: industryKey === ALL_KEY ? 100 : 50,
        sort: periodValue === 'all' ? 'stars' : 'gained',
        period: periodValue === 'all' ? undefined : (periodValue as Period),
        ...(industryKey !== ALL_KEY ? { industry: industryKey } : {}),
      } as any);
      setData(res);
    } catch (e) {
      message.error('加载工具列表失败: ' + (e as Error).message);
    } finally {
      setLoadingRepos(false);
    }
  }

  useEffect(() => {
    loadStats(period);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period]);

  useEffect(() => {
    loadRepos(active, period);
    setFavSet(new Set(JSON.parse(localStorage.getItem('find-github-favorites') || '[]')));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, period]);

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
        await loadRepos(active, period);
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

  const hasPeriod = period && period !== 'all';
  const totalTools = stats.reduce((sum, s) => sum + s.count, 0);
  const maxCount = Math.max(1, ...stats.map((s) => s.count));

  const columns: ColumnsType<Repository> = [
    {
      title: '工具名',
      dataIndex: 'full_name',
      ellipsis: true,
      render: (name: string, record: Repository) => (
        <div className="row-accent" style={{ paddingLeft: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <Link
              to={`/repos/${record.id}`}
              style={{ fontWeight: 500, color: 'var(--text)', border: 'none' }}
            >
              {name}
            </Link>
            {favSet.has(record.id) && <Tag color="gold" style={{ marginRight: 0 }}>★</Tag>}
            {record.is_chinese_owner && <Tag color="red" style={{ marginRight: 0 }}>国产</Tag>}
          </div>
          <div className="repo-desc">{record.description || '—'}</div>
        </div>
      ),
    },
    {
      title: 'AI 中文解读',
      width: 240,
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
            title={<div style={{ whiteSpace: 'pre-wrap' }}>{interp.value_prop || ''}</div>}
            placement="topLeft"
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
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
      title: '行业',
      width: 90,
      dataIndex: 'industry',
      render: (ind: string | null) => <Tag color="purple">{industryLabel(ind)}</Tag>,
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
            title: '新增 ★',
            width: 95,
            dataIndex: 'stars_gained' as keyof Repository,
            render: (v: number | null) =>
              v != null && v > 0 ? (
                <span className="numeric rise-glow" style={{ fontWeight: 700, color: 'var(--accent-bright)', textShadow: '0 0 10px var(--accent-glow)' }}>
                  +{v.toLocaleString()}
                </span>
              ) : (
                <Text type="secondary">—</Text>
              ),
          },
        ]
      : []),
    {
      title: '★ Star',
      width: 95,
      dataIndex: 'stargazers_count',
      sorter: true,
      render: (v: number) => (
        <span className="numeric mono" style={{ fontWeight: 600, color: 'var(--ochre-soft)', textShadow: '0 0 8px var(--ochre-glow)' }}>
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
        <a
          href={record.html_url}
          target="_blank"
          rel="noreferrer"
          className="mono"
          style={{ border: 'none' }}
        >
          ↗
        </a>
      ),
    },
  ];

  return (
    <div className="page-shell anim-fade-up">
      {/* compact toolbar — period switcher + tool count */}
      <div className={`page-toolbar scatter-item ${shownReveal.has('toolbar') ? 'is-shown' : ''}`}>
        <div className="page-toolbar-left">
          <span className="signal-chip">
            <span className="dot" />
            {totalTools} TOOLS
          </span>
          <span className="page-toolbar-meta">按行业分类 · 中文 AI 解读</span>
        </div>
        <Segmented
          value={period}
          onChange={(v) => setPeriod(v as Period)}
          options={[
            { label: '全部', value: 'all' },
            { label: '每日', value: 'daily' },
            { label: '每周', value: 'weekly' },
            { label: '每月', value: 'monthly' },
          ]}
        />
      </div>

      {/* industry stat grid — neon glass cards */}
      <Spin spinning={loadingStats} className={`scatter-item ${shownReveal.has('industryCards') ? 'is-shown' : ''}`}>
        <Row gutter={[8, 8]} style={{ marginBottom: 16, flexShrink: 0 }} className="stagger">
          {stats.map((s, idx) => {
            const isActive = active === (s.industry ?? ALL_KEY);
            const ratio = Math.min(1, s.count / maxCount);
            return (
              <Col
                xs={8}
                sm={6}
                md={4}
                lg={3}
                xl={2}
                key={s.industry ?? '__null__'}
                style={{ ['--i' as any]: idx }}
              >
                <div
                  onClick={() => setActive(s.industry ?? ALL_KEY)}
                  className={isActive ? 'glow-border' : ''}
                  style={{
                    height: '100%',
                    cursor: 'pointer',
                    padding: '12px 14px',
                    position: 'relative',
                    background: isActive
                      ? 'linear-gradient(180deg, rgba(66, 99, 235, 0.10), rgba(252, 250, 254, 0.97))'
                      : 'linear-gradient(180deg, rgba(252, 250, 254, 0.95), rgba(252, 250, 254, 0.92))',
                    border: `1px solid ${isActive ? 'var(--accent)' : 'var(--border)'}`,
                    borderRadius: 'var(--radius)',
                    boxShadow: isActive
                      ? 'var(--shadow-md), inset 0 1px 0 rgba(255, 255, 255, 0.6)'
                      : 'var(--shadow-sm)',
                    transition: 'border-color 200ms ease, box-shadow 200ms ease, transform 200ms ease',
                    overflow: 'hidden',
                  }}
                  onMouseEnter={(e) => {
                    if (!isActive) {
                      (e.currentTarget as HTMLElement).style.borderColor = 'var(--accent-soft)';
                      (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)';
                      (e.currentTarget as HTMLElement).style.boxShadow = 'var(--shadow-md)';
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isActive) {
                      (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)';
                      (e.currentTarget as HTMLElement).style.transform = 'translateY(0)';
                      (e.currentTarget as HTMLElement).style.boxShadow = 'var(--shadow-sm)';
                    }
                  }}
                >
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 500,
                      marginBottom: 6,
                      color: isActive ? 'var(--accent-bright)' : 'var(--text)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      fontFamily: 'var(--font-sans)',
                    }}
                  >
                    {industryLabel(s.industry)}
                  </div>
                  <div
                    className="numeric"
                    style={{
                      fontSize: 20,
                      fontWeight: 700,
                      color: isActive ? 'var(--accent-bright)' : 'var(--text)',
                      lineHeight: 1,
                      textShadow: isActive ? '0 0 12px var(--accent-glow)' : 'none',
                    }}
                  >
                    {s.count}
                    <span style={{ fontSize: 10, color: 'var(--text-dim)', marginLeft: 4, fontWeight: 400 }}>
                      个
                    </span>
                  </div>
                  <div className="mini-bar" style={{ marginTop: 8 }}>
                    <span
                      style={{
                        ['--bar-w' as any]: `${ratio * 100}%`,
                        background: isActive
                          ? 'linear-gradient(90deg, var(--accent), var(--plum-soft))'
                          : 'linear-gradient(90deg, var(--accent-ink), var(--accent-bright))',
                      }}
                    />
                  </div>
                </div>
              </Col>
            );
          })}
          {stats.length === 0 && !loadingStats && (
            <Col span={24}>
              <Text type="secondary">暂无行业统计数据</Text>
            </Col>
          )}
        </Row>
      </Spin>

      {/* industry filter */}
      <div
        style={{
          flexShrink: 0,
          padding: '12px 16px',
          background: 'rgba(252, 250, 254, 0.97)',
          border: '1px solid var(--border)',
          marginBottom: 12,
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-sm)',
          backdropFilter: 'blur(8px)',
        }}
      >
        <Space wrap>
          <span className="label-mono">行业</span>
          <Segmented
            size="small"
            value={active}
            onChange={(v) => setActive(v as string)}
            options={segmentOptions}
          />
        </Space>
      </div>

      <Card bodyStyle={{ padding: 0 }} className={`scatter-item ${shownReveal.has('table') ? 'is-shown' : ''}`} style={{ flex: 1, minHeight: 0 }}>
        <div className={`list-fade ${loadingRepos && data ? 'list-fade-out' : ''}`}>
        <Table<Repository>
          rowKey="id"
          loading={loadingRepos}
          dataSource={data?.items ?? []}
          size="small"
          tableLayout="fixed"
          pagination={{
            pageSize: 20,
            total: data?.total ?? 0,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            showTotal: (total) => `共 ${total} 条`,
            showQuickJumper: true,
            size: 'small',
          }}
          columns={columns}
        />
        </div>
      </Card>
    </div>
  );
}
