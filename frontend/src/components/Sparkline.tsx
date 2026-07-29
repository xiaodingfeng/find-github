import { useMemo } from 'react';

/**
 * Sparkline — a lightweight inline SVG mini-chart for trend visualization.
 * No dependencies, renders a smooth area+line path from a numeric array.
 */
interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  fill?: boolean;
  strokeWidth?: number;
}

export default function Sparkline({
  data,
  width = 80,
  height = 28,
  color = 'var(--accent)',
  fill = true,
  strokeWidth = 1.5,
}: SparklineProps) {
  const { linePath, areaPath, lastX, lastY } = useMemo(() => {
    if (!data || data.length === 0) {
      return { linePath: '', areaPath: '', lastX: 0, lastY: 0 };
    }
    const max = Math.max(...data, 1);
    const min = Math.min(...data, 0);
    const range = max - min || 1;
    const step = data.length > 1 ? width / (data.length - 1) : width;
    const pad = 2;

    const points = data.map((v, i) => {
      const x = i * step;
      const y = pad + (height - pad * 2) * (1 - (v - min) / range);
      return [x, y] as const;
    });

    // smooth path via quadratic curves between midpoints
    let line = `M ${points[0][0]},${points[0][1]}`;
    for (let i = 1; i < points.length; i++) {
      const [px, py] = points[i];
      const [ppx, ppy] = points[i - 1];
      const midX = (ppx + px) / 2;
      const midY = (ppy + py) / 2;
      line += ` Q ${ppx},${ppy} ${midX},${midY}`;
    }
    if (points.length > 1) {
      const last = points[points.length - 1];
      line += ` L ${last[0]},${last[1]}`;
    }

    const area = `${line} L ${width},${height} L 0,${height} Z`;
    const lp = points[points.length - 1];
    return { linePath: line, areaPath: area, lastX: lp[0], lastY: lp[1] };
  }, [data, width, height]);

  if (!data || data.length === 0) {
    return <div style={{ width, height }} />;
  }

  const gradId = useMemo(() => `spark-${Math.random().toString(36).slice(2, 9)}`, []);

  return (
    <svg width={width} height={height} style={{ display: 'block', overflow: 'visible' }}>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.25} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      {fill && <path d={areaPath} fill={`url(#${gradId})`} />}
      <path
        d={linePath}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* end dot */}
      <circle cx={lastX} cy={lastY} r={2} fill={color} />
    </svg>
  );
}
