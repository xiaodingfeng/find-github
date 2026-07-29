import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
// 自托管字体 (替代 Google Fonts CDN), 通过 package.json 管理
import '@fontsource-variable/bricolage-grotesque';
import '@fontsource-variable/dm-sans';
import '@fontsource-variable/dm-sans/wght-italic.css';
import '@fontsource-variable/jetbrains-mono';
import '@fontsource/instrument-serif';
import '@fontsource/instrument-serif/400-italic.css';
import './index.css';

// Ant Design light algorithm + Atelier Galaxy token bridge.
// Most styling is handled by index.css overrides; these tokens cover
// algorithm-derived values (hovers, dropdowns, focus rings, shadows)
// so they align with the light cosmic palette.
const atelierGalaxyTheme = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#4263eb',
    colorInfo: '#4263eb',
    colorSuccess: '#2f9e44',
    colorWarning: '#e67700',
    colorError: '#e8590c',
    colorLink: '#4263eb',
    colorTextBase: '#1e1a2e',
    colorBgBase: '#ece8f0',
    colorBorder: 'rgba(40, 30, 70, 0.10)',
    colorBorderSecondary: 'rgba(40, 30, 70, 0.06)',
    colorBgContainer: 'rgba(255, 255, 255, 0.62)',
    colorBgElevated: 'rgba(255, 255, 255, 0.92)',
    colorBgLayout: 'transparent',
    colorText: '#1e1a2e',
    colorTextSecondary: '#4a4460',
    colorTextTertiary: '#74708a',
    colorTextQuaternary: '#9a96ae',
    colorTextPlaceholder: '#9a96ae',
    fontFamily: "'DM Sans Variable', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
    fontSize: 14,
    borderRadius: 10,
    wireframe: false,
  },
  components: {
    Layout: {
      headerBg: 'transparent',
      headerHeight: 0,
      headerPadding: 0,
      siderBg: 'transparent',
      bodyBg: 'transparent',
      footerBg: 'transparent',
      footerPadding: '12px 0',
    },
    Menu: {
      itemBg: 'transparent',
      subMenuItemBg: 'transparent',
      itemSelectedBg: 'rgba(66, 99, 235, 0.10)',
      itemHoverBg: 'rgba(66, 99, 235, 0.06)',
      itemSelectedColor: '#4263eb',
      itemHeight: 36,
    },
    Card: {
      headerBg: 'transparent',
      headerFontSize: 13,
      paddingLG: 18,
    },
    Table: {
      headerBg: 'rgba(255, 255, 255, 0.5)',
      headerColor: '#4a4460',
      headerSplitColor: 'transparent',
      rowHoverBg: 'rgba(66, 99, 235, 0.05)',
      borderColor: 'rgba(40, 30, 70, 0.10)',
      headerBorderRadius: 14,
      cellPaddingBlock: 12,
      cellPaddingInline: 14,
    },
    Button: {
      primaryShadow: '0 2px 8px rgba(66, 99, 235, 0.3)',
      defaultShadow: '0 1px 2px rgba(30, 26, 46, 0.06)',
    },
    Segmented: {
      itemSelectedColor: '#fff',
      itemSelectedBg: '#4263eb',
      trackBg: 'rgba(255, 255, 255, 0.6)',
      trackPadding: 3,
      borderRadius: 999,
      borderRadiusSM: 999,
    },
    Tag: {
      defaultBg: 'transparent',
    },
    Pagination: {
      itemBg: 'rgba(255, 255, 255, 0.7)',
      itemActiveBg: '#4263eb',
    },
  },
};

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={atelierGalaxyTheme}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
