import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * useDebouncedInput — 受控输入框的防抖 hook.
 *
 * 问题: 输入框 value 绑定到 URL filter (经 updateFilter 同步), 直接防抖 updateFilter
 * 会导致输入框不显示用户打字内容. 本 hook 用本地 state 即时显示, 防抖提交到外部.
 *
 * - local: 即时更新的本地值, 绑定到输入框 value
 * - update: 即时更新 local, 防抖 (默认 400ms) 后调用 onCommit (即 updateFilter)
 * - 当外部 value 变化 (如重置、URL 导航), local 同步并取消待提交的防抖
 */
export function useDebouncedInput<T>(
  value: T,
  onCommit: (v: T) => void,
  delay = 400,
): [T, (v: T) => void] {
  const [local, setLocal] = useState<T>(value);
  const timerRef = useRef<number | undefined>(undefined);
  const onCommitRef = useRef(onCommit);
  onCommitRef.current = onCommit;

  // 外部 value 变化时同步 local, 并取消待提交的防抖 (如点击"重置"时)
  useEffect(() => {
    setLocal(value);
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
  }, [value]);

  const update = useCallback(
    (v: T) => {
      setLocal(v);
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        timerRef.current = undefined;
        onCommitRef.current(v);
      }, delay);
    },
    [delay],
  );

  return [local, update];
}
