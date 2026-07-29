import type { Period } from '../types';

export function periodColor(period: Period | 'daily' | 'weekly' | 'monthly'): string {
  switch (period) {
    case 'daily':
      return '#4263eb';   // royal blue
    case 'weekly':
      return '#2f9e44';   // sage green
    case 'monthly':
      return '#e67700';   // ochre
    default:
      return '#74708a';   // text-dim
  }
}

export function periodLabel(period: Period | string): string {
  switch (period) {
    case 'daily':
      return '每日';
    case 'weekly':
      return '每周';
    case 'monthly':
      return '每月';
    case 'all':
      return '全部';
    default:
      return period;
  }
}
