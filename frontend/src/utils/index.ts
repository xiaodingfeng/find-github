import { periodColor, periodLabel } from './period';

export { periodColor, periodLabel };

// 紧凑数字格式化（与 GitHub 官网一致）：1500 → 1.5k，66988 → 67k，6256580 → 6.3M，11001718 → 11M
const compactFormatter = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

export function formatCompact(n: number | undefined | null): string {
  if (n === undefined || n === null || (typeof n === 'number' && Number.isNaN(n))) return '—';
  return compactFormatter.format(n).replace('K', 'k');
}
