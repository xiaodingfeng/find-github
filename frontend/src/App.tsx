import { useEffect, useDeferredValue, useRef, useState } from 'react';
import { Layout } from 'antd';
import {
  DashboardOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  RadarChartOutlined,
  ThunderboltOutlined,
  CrownOutlined,
} from '@ant-design/icons';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import DashboardPage from './pages/DashboardPage';
import ReposPage from './pages/ReposPage';
import RepoDetailPage from './pages/RepoDetailPage';
import RunsPage from './pages/RunsPage';
import RadarPage from './pages/RadarPage';
import EfficiencyPage from './pages/EfficiencyPage';
import { getSummary } from './api/client';
import type { Summary } from './types';
import GalaxyBackground from './components/GalaxyBackground';

const { Content, Footer } = Layout;

// numbered nav entries — feel like a product launch menu
const menuItems = [
  { key: '/', idx: '01', icon: <DashboardOutlined />, label: '仪表盘', to: '/' },
  { key: '/repos', idx: '02', icon: <DatabaseOutlined />, label: '仓库列表', to: '/repos' },
  { key: '/chinese', idx: '03', icon: <CrownOutlined />, label: '国产开源', to: '/chinese' },
  { key: '/efficiency', idx: '04', icon: <ThunderboltOutlined />, label: '效率工具', to: '/efficiency' },
  { key: '/radar', idx: '05', icon: <RadarChartOutlined />, label: '技术雷达', to: '/radar' },
  { key: '/runs', idx: '06', icon: <HistoryOutlined />, label: '抓取记录', to: '/runs' },
];

function useClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function App() {
  const location = useLocation();
  // 内容区渲染用"延迟值": 点击导航后, 导航 pill 的高亮 (用真实 location, 高优先级)
  // 立即移动, 而整个 <Routes> 子树的卸载+挂载 (antd Table / recharts 初始化开销大)
  // 作为低优先级更新可被中断, 不再阻塞主线程. 消除"点击后卡一下才跳"的体感.
  const deferredLocation = useDeferredValue(location);
  const now = useClock();
  const [summary, setSummary] = useState<Summary | null>(null);
  const navRef = useRef<HTMLElement>(null);
  const itemRefs = useRef<(HTMLAnchorElement | null)[]>([]);
  // sliding pill position — measured from the active item's offsetLeft/offsetWidth
  const [pill, setPill] = useState({ left: 0, width: 0, ready: false });

  // load summary once (silent — used for the masthead stat line)
  useEffect(() => {
    getSummary()
      .then((s) => setSummary(s))
      .catch(() => {});
  }, []);

  const selectedKey =
    location.pathname === '/'
      ? '/'
      : menuItems.find((m) => m.key !== '/' && location.pathname.startsWith(m.key))?.key || '/';

  // 路由切换时回到顶部. 依赖 deferredLocation (而非即时 location):
  // Routes 用 deferredLocation 渲染, 新页面在 deferred 更新后才挂载.
  // 此时滚动才不会让旧页面在卸载前突然跳到页首.
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [deferredLocation.pathname]);

  // measure the active nav item and slide the highlight pill onto it.
  // re-measures on route change, resize, and after fonts settle.
  function measurePill() {
    const idx = menuItems.findIndex((m) => m.key === selectedKey);
    const el = itemRefs.current[idx];
    const nav = navRef.current;
    if (!el || !nav) return;
    const navRect = nav.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    setPill({
      left: elRect.left - navRect.left + nav.scrollLeft,
      width: elRect.width,
      ready: true,
    });
  }
  useEffect(() => {
    measurePill();
    // fonts may shift widths after first paint; re-measure once settled
    const t = window.setTimeout(measurePill, 200);
    window.addEventListener('resize', measurePill);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener('resize', measurePill);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKey]);

  const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });
  const dateStr = now.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });

  const statLine = [
    `${summary?.total_repos?.toLocaleString() ?? '—'} 仓库`,
    `${summary?.chinese_repos?.toLocaleString() ?? '—'} 国产`,
    `${summary?.interpreted_repos?.toLocaleString() ?? '—'} AI 解读`,
  ].join(' · ');

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* ───────── animated galaxy background (canvas particle system) ───────── */}
      <div className="gh-bg" />
      <GalaxyBackground />

      {/* ───────── floating glass top nav ───────── */}
      <header className="gh-nav">
        <div className="gh-nav-inner">
          {/* brand */}
          <Link to="/" className="gh-nav-brand" style={{ borderBottom: 'none' }}>
            <span className="gh-nav-brand-mark">find-github</span>
            <span className="gh-nav-brand-sub">PULSE</span>
          </Link>

          {/* nav — numbered pill tabs with sliding highlight background */}
          <nav className="gh-nav-items" ref={navRef}>
            {/* sliding highlight pill — absolutely positioned, animates
                left/width when the active item changes */}
            <span
              className="gh-nav-pill"
              aria-hidden
              style={{
                left: pill.left,
                width: pill.width,
                opacity: pill.ready ? 1 : 0,
              }}
            />
            {menuItems.map((m, i) => (
              <Link
                key={m.key}
                ref={(el) => { itemRefs.current[i] = el; }}
                to={m.to}
                className={`gh-nav-item ${selectedKey === m.key ? 'active' : ''}`}
              >
                <span className="nav-idx">{m.idx}</span>
                <span className="nav-icon" style={{ display: 'inline-flex', alignItems: 'center' }}>{m.icon}</span>
                <span className="nav-label">{m.label}</span>
              </Link>
            ))}
          </nav>

          {/* aside — live clock */}
          <div className="gh-nav-aside">
            <div className="gh-nav-clock">
              <span className="live-dot" />
              <span>{dateStr}</span>
              <span style={{ color: 'var(--accent-bright)' }}>{timeStr}</span>
            </div>
          </div>
        </div>
      </header>

      {/* ───────── main reading column ───────── */}
      <Layout style={{ background: 'transparent' }}>
        <Content
          style={{
            padding: '28px 32px 32px',
            maxWidth: 1480,
            margin: '0 auto',
            width: '100%',
          }}
        >
          <div key={deferredLocation.pathname} className="anim-fade-in" style={{ minHeight: 'calc(100vh - 220px)' }}>
            <Routes location={deferredLocation}>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/repos" element={<ReposPage />} />
              <Route path="/repos/:id" element={<RepoDetailPage />} />
              <Route path="/chinese" element={<ReposPage initialRegion="china" pageTitle="国产开源" />} />
              <Route path="/efficiency" element={<EfficiencyPage />} />
              <Route path="/radar" element={<RadarPage />} />
              <Route path="/runs" element={<RunsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </Content>

        <Footer
          style={{
            textAlign: 'left',
            padding: '22px 32px',
            borderTop: '1px solid var(--border)',
            background: 'rgba(255, 255, 255, 0.5)',
            backdropFilter: 'blur(12px)',
            zIndex: -1,
          }}
        >
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 20,
              justifyContent: 'space-between',
              alignItems: 'center',
              fontSize: 12,
              color: 'var(--text-dim)',
              maxWidth: 1480,
              margin: '0 auto',
            }}
          >
            <span style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}>
              <a
                href="https://github.com/xiaodingfeng/find-github"
                target="_blank"
                rel="noreferrer"
                style={{ borderBottom: 'none' }}
              >
                <span style={{ color: 'var(--accent)', fontWeight: 700 }}>find-github</span>
                {' · '}中国开发者的开源档案
              </a>
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.04em' }}>
              {statLine}
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.06em' }}>
              数据基于 GitHub Search API + AI 中文解读 · v0.1 · MIT © 2026
            </span>
          </div>
        </Footer>
      </Layout>
    </Layout>
  );
}

export default App;
