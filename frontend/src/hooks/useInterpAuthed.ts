import { useEffect, useState } from 'react';
import { getAdminToken } from '../api/client';

/**
 * useInterpAuthed — 是否已通过「抓取记录」页的管理员密码验证.
 *
 * 用于决定「生成 AI 解读」按钮是否渲染. 未授权时按钮完全不渲染 (无感知),
 * 表格字段直接留空, 不暴露可生成的能力.
 *
 * 授权 token 由 RunsPage 验证通过后写入 localStorage (跨标签页/浏览器重启共享),
 * 过期时间由后端签发时写入 token payload (SESSION_TTL_HOURS, 默认 12 小时),
 * 前端读取时自动校验过期并清除. 监听 admin-token-change 事件和 storage 事件
 * 实时跨标签页同步.
 */
export function useInterpAuthed(): boolean {
  const [authed, setAuthed] = useState(() => !!getAdminToken());

  useEffect(() => {
    const sync = () => setAuthed(!!getAdminToken());
    // 同标签页: setAdminToken/clearAdminToken 触发自定义事件
    window.addEventListener('admin-token-change', sync);
    // 跨标签页: localStorage 变更触发 storage 事件
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'find-github-admin-token') sync();
    };
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener('admin-token-change', sync);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  return authed;
}
