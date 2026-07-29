import { useEffect, useState } from 'react';
import {
  Button,
  Card,
  Input,
  Modal,
  Popconfirm,
  Table,
  message,
} from 'antd';
import { LockOutlined, RobotOutlined, ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  cancelRun,
  getInterpretProgress,
  getRuns,
  triggerCrawl,
  triggerInterpretAll,
  verifyAdmin,
} from '../api/client';
import type { InterpretProgress } from '../api/client';
import type { CrawlRun, CrawlRunList, Period } from '../types';
import { useScatterReveal } from '../hooks/useScatterReveal';
import { useInterpAuthed } from '../hooks/useInterpAuthed';

const statusColor: Record<string, string> = {
  running: 'var(--sage-soft)',
  success: 'var(--accent-bright)',
  failed: 'var(--coral-soft)',
  cancelled: 'var(--ochre-soft)',
};

const statusLabel: Record<string, string> = {
  running: '运行中',
  success: '成功',
  failed: '失败',
  cancelled: '已停止',
};

const periodLabel: Record<string, string> = {
  daily: '每日',
  weekly: '每周',
  monthly: '每月',
};

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function durationLabel(start: string, end: string | null): string {
  if (!end) return '进行中';
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 0) return '—';
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const remS = s % 60;
  if (m < 60) return `${m}m${remS}s`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return `${h}h${remM}m`;
}

export default function RunsPage() {
  const [data, setData] = useState<CrawlRunList | null>(null);
  const [loading, setLoading] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [cancellingId, setCancellingId] = useState<number | null>(null);
  // authed 状态由 useInterpAuthed 统一管理 (基于 sessionStorage token)
  const authed = useInterpAuthed();
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [interpretAllLoading, setInterpretAllLoading] = useState(false);
  const [interpProgress, setInterpProgress] = useState<InterpretProgress | null>(null);
  // entrance animation — random scatter reveal of toolbar / progress / table
  const { shown: shownReveal } = useScatterReveal(
    ['toolbar', 'progressPanel', 'table'],
    200,
  );

  async function load() {
    setLoading(true);
    try {
      const r = await getRuns({ per_page: 50 });
      setData(r);
    } catch (e) {
      message.error('加载失败: ' + (e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // 如果有正在运行的任务, 自动每 5 秒刷新一次 (从 2s 放宽到 5s 减少接口压力)
  useEffect(() => {
    const hasRunning = (data?.items ?? []).some((r) => r.status === 'running');
    if (!hasRunning) return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [data]);

  // 轮询全量 AI 解读进度 (仅 running 时, 间隔 3s)
  useEffect(() => {
    if (!interpProgress?.running) return;
    const timer = setInterval(async () => {
      try {
        const p = await getInterpretProgress();
        setInterpProgress(p);
        if (!p.running) {
          message.success(
            `AI 解读完成: 共 ${p.total}, 成功 ${p.success}, 失败 ${p.failed}, 成功率 ${p.success_rate}%`,
          );
        }
      } catch {
        // 忽略轮询错误
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [interpProgress?.running]);

  // 仅在挂载时拉取一次进度, 不再无条件轮询
  useEffect(() => {
    getInterpretProgress().then(setInterpProgress).catch(() => {});
  }, []);

  function requireAuth(): boolean {
    if (authed) return true;
    setAuthModalOpen(true);
    return false;
  }

  async function handleVerify() {
    setVerifying(true);
    try {
      const res = await verifyAdmin(password);
      // verifyAdmin 内部已通过 setAdminToken 持久化 token,
      // useInterpAuthed 监听事件自动刷新 authed 状态
      if (res.ok) {
        setAuthModalOpen(false);
        setPassword('');
        message.success('验证通过');
      } else {
        message.error(res.message || '密码错误');
      }
    } catch (e) {
      message.error('验证失败: ' + (e as Error).message);
    } finally {
      setVerifying(false);
    }
  }

  async function handleTrigger(period: 'daily' | 'weekly' | 'monthly') {
    if (!requireAuth()) return;
    setTriggering(true);
    try {
      const res = await triggerCrawl(period);
      message.success(res.message);
      setTimeout(load, 500);
    } catch (e) {
      message.error('触发失败: ' + (e as Error).message);
    } finally {
      setTriggering(false);
    }
  }

  async function handleCancel(runId: number) {
    if (!requireAuth()) return;
    setCancellingId(runId);
    try {
      const res = await cancelRun(runId);
      message.success(res.message);
      setTimeout(load, 1000);
    } catch (e) {
      message.error('停止失败: ' + (e as Error).message);
    } finally {
      setCancellingId(null);
    }
  }

  async function handleInterpretAll() {
    if (!requireAuth()) return;
    setInterpretAllLoading(true);
    try {
      await triggerInterpretAll();
      const p = await getInterpretProgress();
      setInterpProgress(p);
      if (p.running) {
        message.success('全量 AI 解读已启动, 进度如下方所示');
      } else if (p.total === 0) {
        message.warning('未启动 (可能已有解读任务在运行, 或 LLM 未启用)');
      }
    } catch (e) {
      message.error('启动失败: ' + (e as Error).message);
    } finally {
      setInterpretAllLoading(false);
    }
  }

  const items = data?.items ?? [];
  const runningCount = items.filter((r) => r.status === 'running').length;
  const successCount = items.filter((r) => r.status === 'success').length;
  const failedCount = items.filter((r) => r.status === 'failed').length;

  const columns: ColumnsType<CrawlRun> = [
    {
      title: '编号',
      dataIndex: 'id',
      width: 70,
      render: (id: number) => (
        <span className="numeric" style={{ color: 'var(--text-dim)', fontSize: 12 }}>
          #{String(id).padStart(4, '0')}
        </span>
      ),
    },
    {
      title: '周期',
      dataIndex: 'period',
      width: 80,
      render: (p: Period) => (
        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
          {periodLabel[p] ?? String(p)}
        </span>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (s: string) => {
        const color = statusColor[s] ?? 'var(--text-dim)';
        const label = statusLabel[s] ?? String(s);
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 12,
              color,
              fontWeight: 500,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                background: color,
                borderRadius: '50%',
                animation: s === 'running' ? 'blink 1.4s ease-in-out infinite' : undefined,
              }}
            />
            {label}
          </span>
        );
      },
    },
    {
      title: '进度',
      width: 180,
      render: (_: unknown, record: CrawlRun) => {
        if (record.status === 'running') {
          const processed = record.total_repos_upserted ?? 0;
          return (
            <span className="mono" style={{ fontSize: 11, color: 'var(--sage)' }}>
              已抓取 {processed}
              <span style={{ color: 'var(--text-dim)', marginLeft: 8 }}>扫描中…</span>
            </span>
          );
        }
        const found = record.total_repos_found ?? 0;
        const upserted = record.total_repos_upserted ?? 0;
        return (
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            <span style={{ color: 'var(--slate)' }}>发现 {found}</span>
            <span style={{ color: 'var(--text-dim)', margin: '0 6px' }}>·</span>
            <span style={{ color: 'var(--sage)' }}>入库 {upserted}</span>
          </span>
        );
      },
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      width: 160,
      render: (v: string) => (
        <span className="mono" style={{ color: 'var(--text-muted)', fontSize: 11 }}>
          {formatTime(v)}
        </span>
      ),
    },
    {
      title: '耗时',
      width: 90,
      render: (_: unknown, record: CrawlRun) => (
        <span className="numeric" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
          {durationLabel(record.started_at, record.finished_at)}
        </span>
      ),
    },
    {
      title: '阈值',
      width: 70,
      render: (_: unknown, record: CrawlRun) => {
        const t = (record.params as Record<string, unknown> | null)?.threshold;
        return (
          <span className="numeric" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
            {t == null ? '—' : String(t)}
          </span>
        );
      },
    },
    {
      title: '错误 / 日志',
      dataIndex: 'error_message',
      ellipsis: true,
      render: (v: string | null) =>
        v ? (
          <span className="mono" style={{ color: 'var(--accent)', fontSize: 11 }}>
            {v}
          </span>
        ) : (
          <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>—</span>
        ),
    },
    {
      title: '操作',
      width: 80,
      render: (_: unknown, record: CrawlRun) =>
        record.status === 'running' ? (
          <Popconfirm
            title={`停止运行 #${record.id}?`}
            onConfirm={() => handleCancel(record.id)}
            okText="停止"
            cancelText="取消"
          >
            <Button size="small" danger loading={cancellingId === record.id}>
              停止
            </Button>
          </Popconfirm>
        ) : (
          <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>·</span>
        ),
    },
  ];

  return (
    <div className="page-shell anim-fade-up">
      {/* compact toolbar — action buttons + run summary */}
      <div className={`page-toolbar scatter-item ${shownReveal.has('toolbar') ? 'is-shown' : ''}`}>
        <div className="page-toolbar-left">
          <span className="signal-chip">
            <span className="dot" />
            {items.length} RUNS
          </span>
          <span className="page-toolbar-meta">
            {runningCount} 运行中 · {successCount} 成功 · {failedCount} 失败
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {authed ? (
            <>
              <Popconfirm
                title="触发每日抓取?"
                onConfirm={() => handleTrigger('daily')}
              >
                <Button loading={triggering}>每日</Button>
              </Popconfirm>
              <Popconfirm
                title="触发每周抓取?"
                onConfirm={() => handleTrigger('weekly')}
              >
                <Button loading={triggering}>每周</Button>
              </Popconfirm>
              <Popconfirm
                title="触发每月抓取?"
                onConfirm={() => handleTrigger('monthly')}
              >
                <Button loading={triggering}>每月</Button>
              </Popconfirm>
              <Popconfirm
                title="批量生成全部仓库的 AI 解读?"
                onConfirm={handleInterpretAll}
                okText="开始"
                cancelText="取消"
              >
                <Button icon={<RobotOutlined />} loading={interpretAllLoading}>
                  全量 AI 解读
                </Button>
              </Popconfirm>
            </>
          ) : (
            <Button icon={<LockOutlined />} onClick={() => setAuthModalOpen(true)}>
              授权
            </Button>
          )}
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
        </div>
      </div>

      {/* ───────── AUTH HINT (when not authed) ───────── */}
      {!authed && (
        <div
          style={{
            flexShrink: 0,
            border: '1px solid rgba(210, 153, 34, 0.35)',
            background: 'linear-gradient(180deg, rgba(210, 153, 34, 0.10), rgba(252, 250, 254, 0.96))',
            padding: '12px 16px',
            marginBottom: 16,
            fontSize: 13,
            color: 'var(--text-muted)',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            borderRadius: 'var(--radius)',
          }}
        >
          <LockOutlined style={{ fontSize: 13, color: 'var(--ochre-soft)' }} />
          <span>
            只读模式 · 点击 <span style={{ color: 'var(--accent-bright)', fontWeight: 600 }}>授权</span> 输入密码后可触发抓取、停止任务与全量 AI 解读
          </span>
        </div>
      )}

      {/* ───────── INTERPRET PROGRESS PANEL ───────── */}
      {interpProgress && (interpProgress.running || interpProgress.total > 0) && (
        <div
          className={`scatter-item ${shownReveal.has('progressPanel') ? 'is-shown' : ''}`}
          style={{
            flexShrink: 0,
            position: 'relative',
            border: `1px solid ${interpProgress.running ? 'rgba(121, 192, 255, 0.4)' : interpProgress.error ? 'rgba(240, 136, 62, 0.4)' : 'rgba(63, 185, 80, 0.4)'}`,
            background: interpProgress.running
              ? 'linear-gradient(180deg, rgba(121, 192, 255, 0.10), rgba(252, 250, 254, 0.96))'
              : interpProgress.error
              ? 'linear-gradient(180deg, rgba(240, 136, 62, 0.10), rgba(252, 250, 254, 0.96))'
              : 'linear-gradient(180deg, rgba(63, 185, 80, 0.10), rgba(252, 250, 254, 0.96))',
            padding: '16px 20px',
            marginBottom: 16,
            borderRadius: 'var(--radius-lg)',
          }}
        >
          {/* header row */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 12,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <RobotOutlined style={{ color: 'var(--accent)', fontSize: 14 }} />
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: interpProgress.running
                    ? 'var(--slate)'
                    : interpProgress.error
                    ? 'var(--accent)'
                    : 'var(--sage)',
                }}
              >
                {interpProgress.running
                  ? 'AI 解读进行中'
                  : interpProgress.error
                  ? 'AI 解读出错'
                  : 'AI 解读已完成'}
              </span>
            </div>
            <div className="numeric" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              {interpProgress.processed} / {interpProgress.total}
              <span style={{ color: 'var(--text-dim)', margin: '0 8px' }}>·</span>
              <span style={{ color: 'var(--accent)', fontWeight: 600 }}>
                {interpProgress.progress_percent}%
              </span>
            </div>
          </div>

          {/* current repo line */}
          {interpProgress.running && interpProgress.current_repo && (
            <div
              className="mono"
              style={{
                fontSize: 11,
                color: 'var(--text-dim)',
                marginBottom: 10,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              正在处理 <span style={{ color: 'var(--text)' }}>{interpProgress.current_repo}</span>
            </div>
          )}

          {/* progress bar — soft pill */}
          <div
            style={{
              position: 'relative',
              height: 6,
              background: 'var(--surface-2)',
              marginBottom: 12,
              borderRadius: 'var(--radius-pill)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                position: 'absolute',
                left: 0,
                top: 0,
                bottom: 0,
                width: `${interpProgress.progress_percent}%`,
                background: interpProgress.running
                  ? 'var(--slate)'
                  : interpProgress.error
                  ? 'var(--accent)'
                  : 'var(--sage)',
                transition: 'width 400ms ease',
                borderRadius: 'var(--radius-pill)',
              }}
            />
          </div>

          {/* stats footer */}
          <div
            style={{
              display: 'flex',
              gap: 20,
              fontSize: 11,
              color: 'var(--text-dim)',
              flexWrap: 'wrap',
            }}
          >
            <span>
              <span style={{ color: 'var(--sage)', fontWeight: 600 }}>成功</span>{' '}
              {interpProgress.success}
            </span>
            <span>
              <span style={{ color: 'var(--accent)', fontWeight: 600 }}>失败</span>{' '}
              {interpProgress.failed}
            </span>
            <span>
              <span style={{ color: 'var(--text-muted)' }}>成功率</span>{' '}
              {interpProgress.success_rate}%
            </span>
            {interpProgress.error && (
              <span style={{ color: 'var(--accent)' }}>{interpProgress.error}</span>
            )}
          </div>
        </div>
      )}

      {/* ───────── METRIC STRIP ───────── */}
      <div className="metric-strip" style={{ marginBottom: 16, flexShrink: 0 }}>
        <div>
          <div className="metric-label">总条目</div>
          <div className="metric-value">{items.length}</div>
        </div>
        <div>
          <div className="metric-label">运行中</div>
          <div className="metric-value metric-value-green">
            {runningCount}
            {runningCount > 0 && (
              <span className="blink" style={{ marginLeft: 8, fontSize: 12, color: 'var(--sage)' }}>
                ●
              </span>
            )}
          </div>
        </div>
        <div>
          <div className="metric-label">成功</div>
          <div className="metric-value metric-value-cyan">{successCount}</div>
        </div>
        <div>
          <div className="metric-label">失败</div>
          <div className="metric-value metric-value-hot">{failedCount}</div>
        </div>
        <div>
          <div className="metric-label">入库总数</div>
          <div className="metric-value">
            {items.reduce((s, r) => s + (r.total_repos_upserted ?? 0), 0).toLocaleString()}
          </div>
        </div>
        <div>
          <div className="metric-label">权限</div>
          <div
            className="mono"
            style={{
              fontSize: 14,
              fontWeight: 700,
              marginTop: 6,
              color: authed ? 'var(--sage)' : 'var(--accent)',
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            {authed ? '管理员' : '访客'}
          </div>
        </div>
      </div>

      {/* ───────── RUNS TABLE ───────── */}
      <Card bodyStyle={{ padding: 0 }} className={`scatter-item ${shownReveal.has('table') ? 'is-shown' : ''}`} style={{ flex: 1, minHeight: 0 }}>
        <div className={`list-fade ${loading && data ? 'list-fade-out' : ''}`}>
        <Table<CrawlRun>
          rowKey="id"
          loading={loading}
          dataSource={items}
          size="small"
          tableLayout="fixed"
          pagination={{
            pageSize: 20,
            total: data?.total ?? 0,
            showTotal: (t) => `共 ${t} 条`,
            showSizeChanger: false,
            showQuickJumper: true,
            size: 'small',
          }}
          columns={columns}
        />
        </div>
      </Card>

      {/* ───────── AUTH MODAL ───────── */}
      <Modal
        title="管理员授权"
        open={authModalOpen}
        onOk={handleVerify}
        onCancel={() => {
          setAuthModalOpen(false);
          setPassword('');
        }}
        confirmLoading={verifying}
        okText="验证"
        cancelText="取消"
      >
        <div
          style={{
            marginBottom: 14,
            padding: '12px 16px',
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            fontSize: 13,
            color: 'var(--text-muted)',
            lineHeight: 1.6,
            borderRadius: 'var(--radius)',
          }}
        >
          <div>输入管理员密码以解锁以下操作:</div>
          <div>触发抓取 · 停止任务 · 全量 AI 解读</div>
        </div>
        <Input.Password
          placeholder="管理员密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onPressEnter={handleVerify}
          autoFocus
        />
      </Modal>
    </div>
  );
}
