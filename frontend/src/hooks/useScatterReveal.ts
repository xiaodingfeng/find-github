import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * useScatterReveal — reveals a list of element IDs in random order with
 * irregular gaps, so items "scatter in" rather than appearing as a block.
 *
 * Each element gets its own random delay (0-250ms after the previous one),
 * and the reveal order is shuffled. This produces the trickling-in entrance
 * effect used across all pages.
 *
 * @param ids      stable list of element IDs to reveal (e.g. ['hero','chart1'])
 * @param startMs  initial delay before the first reveal (default 300ms)
 * @returns { shown, reveal, reset } — shown is a Set of currently-visible IDs
 */
export function useScatterReveal(ids: string[], startMs = 300) {
  const [shown, setShown] = useState<Set<string>>(new Set());
  const timersRef = useRef<number[]>([]);

  const reveal = useCallback((id: string) => {
    setShown((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    timersRef.current.forEach((t) => window.clearTimeout(t));
    timersRef.current = [];
    setShown(new Set());
  }, []);

  useEffect(() => {
    // clear previous run
    timersRef.current.forEach((t) => window.clearTimeout(t));
    timersRef.current = [];
    setShown(new Set());

    if (ids.length === 0) return;

    // shuffle a copy so the reveal order is random each time
    const order = [...ids];
    for (let i = order.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [order[i], order[j]] = [order[j], order[i]];
    }

    let acc = startMs;
    order.forEach((id) => {
      // random gap 0-250ms between consecutive reveals
      acc += Math.random() * 250;
      const t = window.setTimeout(() => reveal(id), acc);
      timersRef.current.push(t);
    });

    return () => {
      timersRef.current.forEach((t) => window.clearTimeout(t));
      timersRef.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids.join('|'), startMs]);

  return { shown, reveal, reset };
}
