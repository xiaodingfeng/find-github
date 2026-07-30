import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Button,
  Card,
  Col,
  Descriptions,
  Rate,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd';
import { ArrowLeftOutlined, StarOutlined, ForkOutlined, RobotOutlined, ReloadOutlined, EyeOutlined, WarningOutlined, FileTextOutlined, CloseOutlined, TranslationOutlined, GlobalOutlined, RollbackOutlined } from '@ant-design/icons';
import { Link, useParams } from 'react-router-dom';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { getRepo, getRepoInterpretations, getRepoReadme, translateRepoReadme, toggleFavorite, triggerInterpretSync } from '../api/client';
import type { AIInterpretation, RepositoryDetail } from '../types';
import { useScatterReveal } from '../hooks/useScatterReveal';
import { useInterpAuthed } from '../hooks/useInterpAuthed';
import { periodColor, periodLabel } from '../utils';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

const { Paragraph, Text } = Typography;

export default function RepoDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [repo, setRepo] = useState<RepositoryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [isFav, setIsFav] = useState(false);
  const [interpretations, setInterpretations] = useState<AIInterpretation[]>([]);
  const [interpLoading, setInterpLoading] = useState(false);

  // README 弹框: mounted 控制是否渲染, visible 控制 fade in/out; position: fixed 固定悬浮在导航栏下方
  // readmeTop 相对屏幕, 取 .gh-nav 底部 + 15px 间距, 因导航栏 sticky 故不受页面滚动影响
  const [readmeMounted, setReadmeMounted] = useState(false);
  const [readmeVisible, setReadmeVisible] = useState(false);
  const [readmeContent, setReadmeContent] = useState('');
  const [readmeLoading, setReadmeLoading] = useState(false);
  const [readmeTop, setReadmeTop] = useState(90);
  // AI 翻译: originalContent 保留原文, translatedContent 缓存译文, showTranslated 切换显示
  const [originalContent, setOriginalContent] = useState('');
  const [translatedContent, setTranslatedContent] = useState('');
  const [showTranslated, setShowTranslated] = useState(false);
  const [translating, setTranslating] = useState(false);
  // entrance animation — random scatter reveal of header / stats / charts / interp
  const { shown: shownReveal } = useScatterReveal(
    ['header', 'stats', 'chart', 'interp'],
    200,
  );
  const interpAuthed = useInterpAuthed();

  async function loadInterpretations() {
    if (!id) return;
    try {
      const data = await getRepoInterpretations(Number(id));
      setInterpretations(data);
    } catch (e) {
      // silent
    }
  }

  useEffect(() => {
    if (!id) return;
    // scroll to top on detail page mount — prevents the browser from
    // keeping the list page's scroll position (which appears as "scrolled to bottom")
    window.scrollTo(0, 0);
    setLoading(true);
    getRepo(Number(id))
      .then((r) => {
        setRepo(r);
        setIsFav(JSON.parse(localStorage.getItem('find-github-favorites') || '[]').includes(r.id));
      })
      .catch((e) => message.error('加载失败: ' + (e as Error).message))
      .finally(() => setLoading(false));
    loadInterpretations();
  }, [id]);

  async function handleGenerateInterp() {
    if (!repo) return;
    if (!interpAuthed) return;
    setInterpLoading(true);
    try {
      const result = await triggerInterpretSync([repo.id], 1, true);
      if (result.success > 0) {
        message.success('AI 解读已生成');
      } else {
        message.warning('AI 解读未生成, 请检查后端 LLM 配置 (LLM_API_KEY / LLM_API_BASE)');
      }
      await loadInterpretations();
    } catch (e) {
      message.error('同步生成 AI 解读失败: ' + (e as Error).message);
    } finally {
      setInterpLoading(false);
    }
  }

  // 点击"查看 README": 取导航栏底部位置 +15px 作为弹框顶部, 挂载弹框, 调后端拿 README (后端 5 分钟缓存)
  async function handleViewReadme() {
    if (!repo) return;
    const navEl = document.querySelector('.gh-nav');
    if (navEl) {
      setReadmeTop(Math.round(navEl.getBoundingClientRect().bottom) + 15);
    }
    setReadmeMounted(true);
    // 下一帧再设 visible, 触发 fade-in 过渡
    requestAnimationFrame(() => setReadmeVisible(true));
    // 已有缓存的原文, 直接显示 (保留之前的翻译状态)
    if (originalContent) {
      setReadmeContent(showTranslated && translatedContent ? translatedContent : originalContent);
      return;
    }
    setReadmeLoading(true);
    try {
      const result = await getRepoReadme(repo.id);
      setOriginalContent(result.content);
      setReadmeContent(result.content);
    } catch (e) {
      message.error('README 加载失败: ' + (e as Error).message);
      setReadmeVisible(false);
      setTimeout(() => setReadmeMounted(false), 320);
    } finally {
      setReadmeLoading(false);
    }
  }

  function handleCloseReadme() {
    setReadmeVisible(false);
    setTimeout(() => setReadmeMounted(false), 320);
  }

  // README 弹框为 position: fixed 悬浮层, 不锁定 body 滚动 — 详情页与 README
  // 可同时滚动. README body 有 overscroll-behavior: contain 防止滚动穿透.

  // 检测文本是否主要为中文 (CJK 字符占比 > 30% 则认为是中文文档, 不显示翻译按钮)
  function isMostlyChinese(text: string): boolean {
    if (!text) return false;
    const cjkCount = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
    const totalChars = text.replace(/\s/g, '').length;
    return totalChars > 0 && cjkCount / totalChars > 0.3;
  }

  // AI 翻译 README
  async function handleTranslate() {
    if (!repo || !originalContent) return;
    // 已有译文缓存, 直接切换
    if (translatedContent) {
      setShowTranslated(true);
      setReadmeContent(translatedContent);
      return;
    }
    setTranslating(true);
    try {
      const result = await translateRepoReadme(repo.id);
      setTranslatedContent(result.content);
      setShowTranslated(true);
      setReadmeContent(result.content);
      if (result.cached) {
        message.success('已从缓存加载翻译');
      }
    } catch (e) {
      message.error('AI 翻译失败: ' + (e as Error).message);
    } finally {
      setTranslating(false);
    }
  }

  // 切回原文
  function handleShowOriginal() {
    setShowTranslated(false);
    setReadmeContent(originalContent);
  }

  // 渲染 README markdown: marked 解析后经 DOMPurify 净化, 防止存储型 XSS (C2).
  // README 内容来自任意 GitHub 仓库 + LLM 翻译输出, 均为不可信数据.
  function renderReadmeHtml(): string {
    try {
      const rawHtml = marked.parse(readmeContent) as string;
      return DOMPurify.sanitize(rawHtml, {
        // 允许常见 markdown 渲染所需的标签, 禁止 script/iframe/form 等
        FORBID_TAGS: ['script', 'iframe', 'form', 'object', 'embed', 'link', 'style'],
        FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'onfocus', 'onblur'],
      });
    } catch {
      // catch 分支也必须净化, 避免 </pre><script> 闭合标签注入
      const escaped = readmeContent
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      return `<pre style="white-space:pre-wrap;">${escaped}</pre>`;
    }
  }

  if (loading) {
    return <Spin size="large" style={{ display: 'block', marginTop: 100 }} />;
  }
  if (!repo) {
    return (
      <div className="anim-fade-in">
        <div className="eyebrow" style={{ marginBottom: 12 }}>§ 404 · 档案缺失</div>
        <Paragraph>仓库不存在或已从档案中移除。</Paragraph>
        <Link to="/repos"><Button icon={<ArrowLeftOutlined />}>返回索引</Button></Link>
      </div>
    );
  }

  const chartData = repo.snapshots
    .slice()
    .sort((a, b) => (a.snapshot_date < b.snapshot_date ? -1 : 1))
    .map((s) => ({
      date: s.snapshot_date,
      [s.period]: s.stars_at_snapshot,
    }));

  function handleFav() {
    const added = toggleFavorite(repo!.id);
    setIsFav(added);
    message.success(added ? '已收藏' : '已取消收藏');
  }

  const stats = [
    { icon: <StarOutlined />, label: 'Stars', value: repo.stargazers_count, color: 'var(--ochre-soft)', glow: 'var(--ochre-glow)', cls: 'metric-value-amber' },
    { icon: <ForkOutlined />, label: 'Forks', value: repo.forks_count, color: 'var(--slate-soft)', glow: 'rgba(121, 192, 255, 0.4)', cls: 'metric-value-cyan' },
    { icon: <EyeOutlined />, label: 'Watchers', value: repo.watchers_count, color: 'var(--sage-soft)', glow: 'var(--sage-glow)', cls: 'metric-value-green' },
    { icon: <WarningOutlined />, label: 'Open Issues', value: repo.open_issues_count, color: 'var(--coral-soft)', glow: 'rgba(240, 136, 62, 0.4)', cls: 'metric-value-hot' },
  ];

  return (
    <div className="anim-fade-up">
      {/* breadcrumb / actions */}
      <div
        className={`scatter-item ${shownReveal.has('header') ? 'is-shown' : ''}`}
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 14,
          paddingBottom: 12,
          borderBottom: '1px solid var(--border)',
        }}
      >
        <Link to="/repos" className="mono" style={{ fontSize: 11, letterSpacing: '0.12em', color: 'var(--accent)', textTransform: 'uppercase', borderBottom: 'none' }}>
          <ArrowLeftOutlined /> § 02 / 仓库索引
        </Link>
        <Space>
          <Button onClick={handleFav} type={isFav ? 'primary' : 'default'}>
            {isFav ? '★ 已收藏' : '☆ 收藏'}
          </Button>
          <a href={repo.html_url} target="_blank" rel="noreferrer">
            <Button>GITHUB ↗</Button>
          </a>
        </Space>
      </div>

      {/* article header */}
      <div
        className={`scatter-item ${shownReveal.has('stats') ? 'is-shown' : ''} glow-border`}
        style={{
          padding: '28px 28px 24px',
          marginBottom: 20,
          borderBottom: '1px solid var(--border-strong)',
          position: 'relative',
          background: 'linear-gradient(180deg, rgba(252, 250, 254, 0.97), rgba(252, 250, 254, 0.93))',
          borderRadius: 'var(--radius-lg)',
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 12,
            gap: 12,
          }}
        >
          <div className="eyebrow">
            § 档案 · {repo.owner}
            <span style={{ marginLeft: 12, fontSize: 11, color: 'var(--text-dim)', fontWeight: 400 }}>
              入库 {new Date(repo.first_seen_at).toLocaleDateString('zh-CN')}
            </span>
          </div>
          <Button
            size="small"
            icon={<FileTextOutlined />}
            onClick={handleViewReadme}
            loading={readmeLoading && readmeMounted}
          >
            查看 README
          </Button>
        </div>
        <h1
          className="headline"
          style={{
            fontSize: 'clamp(34px, 4.5vw, 46px)',
            lineHeight: 1.05,
            margin: 0,
            color: 'var(--text)',
          }}
        >
          <a href={repo.html_url} target="_blank" rel="noreferrer" style={{ color: 'inherit', borderBottom: 'none' }}>
            {repo.full_name}
          </a>
          <span style={{ color: 'var(--accent)', marginLeft: 10 }}>↗</span>
        </h1>
        <Paragraph
          className="serif"
          style={{
            fontSize: 20,
            lineHeight: 1.45,
            color: 'var(--text-muted)',
            marginTop: 16,
            maxWidth: 780,
          }}
        >
          {repo.description || '（无描述）'}
        </Paragraph>
        <Space wrap size={[6, 6]} style={{ marginTop: 8 }}>
          {/* inline stat badges — stars / forks / watchers / issues (display first) */}
          {stats.map((s) => (
            <Tag
              key={s.label}
              style={{
                marginInlineEnd: 0,
                background: 'rgba(66, 99, 235, 0.06)',
                border: '1px solid rgba(66, 99, 235, 0.15)',
                color: s.color,
                fontFamily: 'var(--font-mono)',
                fontVariantNumeric: 'tabular-nums',
                fontSize: 12,
              }}
            >
              {s.icon} {s.value.toLocaleString()} <span style={{ opacity: 0.6, fontSize: 10 }}>{s.label}</span>
            </Tag>
          ))}
          {repo.language && <Tag color="blue">{repo.language}</Tag>}
          {repo.license && <Tag color="purple">{repo.license}</Tag>}
          {repo.is_chinese_owner && <Tag color="red">国产</Tag>}
          {repo.has_chinese_doc && <Tag color="green">中文文档</Tag>}
          {repo.is_efficiency_tool && <Tag color="purple">效率工具</Tag>}
          {repo.topics?.slice(0, 12).map((t) => (
            <Tag key={t}>{t}</Tag>
          ))}
        </Space>
      </div>

      {/* AI interpretation — editorial pull-quote style */}
      <Card
        className={`scatter-item ${shownReveal.has('interp') ? 'is-shown' : ''}`}
        title={
          <Space>
            <RobotOutlined style={{ color: 'var(--plum)' }} />
            <span>AI 中文解读</span>
            {interpretations[0]?.model && (
              <Tag color="purple">{interpretations[0].model}</Tag>
            )}
          </Space>
        }
        extra={
          interpAuthed ? (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={interpLoading}
              onClick={handleGenerateInterp}
            >
              {interpLoading
                ? 'AI 生成中...'
                : interpretations.length > 0
                  ? '重新生成'
                  : '生成解读'}
            </Button>
          ) : null
        }
        style={{ marginBottom: 20 }}
      >
        {interpretations.length === 0 ? (
          <Paragraph type="secondary" className="serif" style={{ fontSize: 18 }}>
            {interpAuthed
              ? '暂无 AI 解读。点击右上角「生成解读」，AI 将为这个仓库生成中文简介、难度评分、适合人群等内容。'
              : '暂无 AI 解读。'}
          </Paragraph>
        ) : (
          (() => {
            const interp = interpretations[0];
            return (
              <div>
                {interp.summary_cn && (
                  <div className="pull-quote" style={{ fontSize: 24 }}>
                    {interp.summary_cn}
                  </div>
                )}
                {interp.value_prop && (
                  <Paragraph type="secondary" style={{ marginBottom: 16, fontSize: 14, marginTop: 4 }}>
                    <Text strong style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                      § 价值解读 ·{' '}
                    </Text>
                    {interp.value_prop}
                  </Paragraph>
                )}
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col span={8}>
                    <Card size="small">
                      <div className="label-mono" style={{ marginBottom: 8 }}>上手难度</div>
                      {interp.difficulty ? (
                        <Rate disabled value={interp.difficulty} count={5} />
                      ) : (
                        <Text type="secondary">—</Text>
                      )}
                    </Card>
                  </Col>
                  <Col span={8}>
                    <Card size="small">
                      <div className="label-mono" style={{ marginBottom: 8 }}>预计学习时长</div>
                      <Text strong className="serif numeric" style={{ fontSize: 24, color: 'var(--accent)' }}>
                        {interp.learning_hours ? `${interp.learning_hours}h` : '—'}
                      </Text>
                    </Card>
                  </Col>
                  <Col span={8}>
                    <Card size="small">
                      <div className="label-mono" style={{ marginBottom: 8 }}>生成时间</div>
                      <Text type="secondary" className="mono" style={{ fontSize: 11 }}>
                        {new Date(interp.generated_at).toLocaleString('zh-CN')}
                      </Text>
                    </Card>
                  </Col>
                </Row>
                {interp.suitable_for && (
                  <Paragraph style={{ marginBottom: 10 }}>
                    <Text strong style={{ color: 'var(--sage-soft)', fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                      § 适合人群 ·{' '}
                    </Text>
                    {interp.suitable_for}
                  </Paragraph>
                )}
                {interp.alternatives && (
                  <Paragraph>
                    <Text strong style={{ color: 'var(--plum-soft)', fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                      § 替代品 ·{' '}
                    </Text>
                    {interp.alternatives}
                  </Paragraph>
                )}
              </div>
            );
          })()
        )}
      </Card>

      {/* Star trend — animated chart with glow */}
      <Card
        className={`scatter-item ${shownReveal.has('chart') ? 'is-shown' : ''}`}
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)' }} />
            Star 趋势 · 历史快照
          </span>
        }
        style={{ marginBottom: 20 }}
      >
        {chartData.length === 0 ? (
          <Paragraph type="secondary">暂无历史快照数据</Paragraph>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={chartData}>
              <defs>
                <linearGradient id="grad-daily" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={periodColor('daily')} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={periodColor('daily')} stopOpacity={0} />
                </linearGradient>
                <linearGradient id="grad-weekly" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={periodColor('weekly')} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={periodColor('weekly')} stopOpacity={0} />
                </linearGradient>
                <linearGradient id="grad-monthly" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={periodColor('monthly')} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={periodColor('monthly')} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" opacity={0.5} />
              <XAxis dataKey="date" stroke="var(--text-dim)" tick={{ fontFamily: 'var(--font-mono)', fontSize: 10 }} />
              <YAxis stroke="var(--text-dim)" tick={{ fontFamily: 'var(--font-mono)', fontSize: 10 }} />
              <Tooltip
                contentStyle={{
                  background: 'rgba(255, 255, 255, 0.95)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: 'var(--radius)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  color: 'var(--text)',
                  boxShadow: 'var(--shadow-md)',
                  backdropFilter: 'blur(8px)',
                }}
                labelStyle={{ color: 'var(--accent)', fontWeight: 600 }}
                itemStyle={{ color: 'var(--text)' }}
              />
              <Legend
                formatter={(v) => <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-sans)', fontSize: 12 }}>{v}</span>}
              />
              <Line type="monotone" dataKey="daily" stroke={periodColor('daily')} name={periodLabel('daily')} connectNulls strokeWidth={2} dot={{ r: 3, fill: periodColor('daily') }} activeDot={{ r: 6, fill: periodColor('daily'), stroke: '#fff', strokeWidth: 2 }} isAnimationActive animationBegin={200} animationDuration={1200} />
              <Line type="monotone" dataKey="weekly" stroke={periodColor('weekly')} name={periodLabel('weekly')} connectNulls strokeWidth={2} dot={{ r: 3, fill: periodColor('weekly') }} activeDot={{ r: 6, fill: periodColor('weekly'), stroke: '#fff', strokeWidth: 2 }} isAnimationActive animationBegin={400} animationDuration={1200} />
              <Line type="monotone" dataKey="monthly" stroke={periodColor('monthly')} name={periodLabel('monthly')} connectNulls strokeWidth={2} dot={{ r: 3, fill: periodColor('monthly') }} activeDot={{ r: 6, fill: periodColor('monthly'), stroke: '#fff', strokeWidth: 2 }} isAnimationActive animationBegin={600} animationDuration={1200} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Card>

      {/* metadata — colophon style */}
      <Card title="元数据 · METADATA">
        <Descriptions column={2} size="small">
          <Descriptions.Item label="OWNER">{repo.owner}</Descriptions.Item>
          <Descriptions.Item label="OWNER_TYPE">{repo.owner_type || '—'}</Descriptions.Item>
          <Descriptions.Item label="CREATED">
            {repo.created_at ? new Date(repo.created_at).toLocaleString('zh-CN') : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="UPDATED">
            {repo.updated_at ? new Date(repo.updated_at).toLocaleString('zh-CN') : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="PUSHED_AT">
            {repo.pushed_at ? new Date(repo.pushed_at).toLocaleString('zh-CN') : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="FIRST_SEEN">
            {new Date(repo.first_seen_at).toLocaleString('zh-CN')}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* README 弹框: 通过 Portal 渲染到 body, 脱离祖先 transform 影响, 保证 position: fixed 相对视口定位; 固定悬浮在导航栏下方, 顶部距导航栏底部 15px, 底部距屏幕底部 15px, 渐入渐出 */}
      {readmeMounted && createPortal(
        <>
        {/* 透明遮罩: 捕获 README 区域外的点击, 关闭弹框 */}
        <div
          className={`readme-backdrop ${readmeVisible ? 'readme-backdrop-visible' : ''}`}
          onClick={handleCloseReadme}
        />
        <div
          className={`readme-overlay ${readmeVisible ? 'readme-overlay-visible' : ''}`}
          style={{ top: `${readmeTop}px` }}
        >
          <div className="readme-overlay-header">
            <span className="eyebrow">§ README · {repo.full_name}</span>
            <Space size="small">
              {/* AI 翻译按钮: 仅在原文非中文且有内容时显示 */}
              {originalContent && !isMostlyChinese(originalContent) && (
                !showTranslated ? (
                  <Button
                    type="text"
                    size="small"
                    icon={<TranslationOutlined />}
                    loading={translating}
                    onClick={handleTranslate}
                  >
                    {translating ? '翻译中...' : 'AI翻译'}
                  </Button>
                ) : (
                  <Button
                    type="text"
                    size="small"
                    icon={<RollbackOutlined />}
                    onClick={handleShowOriginal}
                  >
                    原文
                  </Button>
                )
              )}
              {/* 翻译状态指示 */}
              {showTranslated && (
                <Tag color="blue" style={{ marginInlineEnd: 0, fontSize: 10 }}>
                  <GlobalOutlined /> 中文翻译
                </Tag>
              )}
              <Button
                type="text"
                size="small"
                icon={<CloseOutlined />}
                onClick={handleCloseReadme}
              />
            </Space>
          </div>
          <div className="readme-overlay-body">
            {readmeLoading && !readmeContent ? (
              <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />
            ) : translating && !translatedContent ? (
              <div style={{ textAlign: 'center', padding: '80px 20px', color: 'var(--text-muted)' }}>
                <Spin size="large" />
                <div style={{ marginTop: 16, fontSize: 13 }}>AI 正在翻译文档, 请稍候...</div>
                <div style={{ marginTop: 4, fontSize: 11, color: 'var(--text-dim)' }}>较长的文档可能需要 30-60 秒</div>
              </div>
            ) : (
              <div
                className="readme-content"
                dangerouslySetInnerHTML={{ __html: renderReadmeHtml() }}
              />
            )}
          </div>
        </div>
        </>,
        document.body,
      )}
    </div>
  );
}
