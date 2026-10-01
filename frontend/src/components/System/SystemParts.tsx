/** Rings, bars and the constellation graph shared by the panel and the tile. */

import { DIM, MUTED, colorFor } from '../../lib/system-style';

export function Ring({
  percent,
  size,
  high,
  label,
  detail,
  focused,
}: {
  percent: number;
  size: number;
  high: boolean;
  label?: string;
  detail?: string;
  focused?: boolean;
}) {
  const r = size / 2 - 9;
  const c = 2 * Math.PI * r;
  const color = colorFor(high);
  const p = Math.max(0, Math.min(100, percent));
  return (
    <div
      className={high ? 'sys-breathe-amber' : focused ? 'sys-breathe' : undefined}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 2,
        borderRadius: 14,
        padding: '8px 0 6px',
        outline: focused ? `1px solid rgba(34,211,238,0.45)` : undefined,
      }}
    >
      <svg width={size} height={size} role="img" aria-label={`${label ?? ''} ${Math.round(p)}%`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#27272a" strokeWidth={7} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={7}
          strokeLinecap="round"
          strokeDasharray={`${(p / 100) * c} ${c}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: 'stroke-dasharray 0.6s', filter: `drop-shadow(0 0 6px ${color}99)` }}
        />
        <text x="50%" y="52%" textAnchor="middle" dominantBaseline="middle" fill="#fafafa" fontSize={size > 90 ? 22 : 13} fontWeight={700}>
          {Math.round(p)}%
        </text>
      </svg>
      {label ? <span style={{ fontSize: 12, color: MUTED }}>{label}</span> : null}
      {detail ? <span style={{ fontSize: 11.5, color: DIM, fontVariantNumeric: 'tabular-nums' }}>{detail}</span> : null}
    </div>
  );
}

export function Bar({ percent, high, height = 7 }: { percent: number; high: boolean; height?: number }) {
  const color = colorFor(high);
  return (
    <div style={{ height, borderRadius: 99, background: '#27272a', overflow: 'hidden' }}>
      <i
        style={{
          display: 'block',
          height: '100%',
          width: `${Math.max(0, Math.min(100, percent))}%`,
          borderRadius: 99,
          background: color,
          boxShadow: `0 0 8px ${color}88`,
          transition: 'width 0.6s',
        }}
      />
    </div>
  );
}

/** The last minute as linked, glowing points (the weather curve's style). */
export function Constellation({ points, high, width = 170, height = 34 }: { points: number[]; high: boolean; width?: number; height?: number }) {
  if (points.length < 2) return <svg width={width} height={height} aria-hidden />;
  const color = colorFor(high);
  const x = (i: number) => ((i + 0.5) * width) / points.length;
  const y = (v: number) => height - 4 - (Math.max(0, Math.min(100, v)) / 100) * (height - 8);
  const line = points.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
      {points.slice(0, -2).map((v, i) => (
        <line key={i} x1={x(i)} y1={y(v)} x2={x(i + 2)} y2={y(points[i + 2])} stroke={color} strokeOpacity={0.12} />
      ))}
      <path d={line} fill="none" stroke={color} strokeOpacity={0.75} strokeWidth={1.3} style={{ filter: `drop-shadow(0 0 3px ${color})` }} />
      {points.map((v, i) =>
        i % 4 === 0 || i === points.length - 1 ? (
          <circle key={`p${i}`} cx={x(i)} cy={y(v)} r={i === points.length - 1 ? 2.6 : 1.5} fill="#cffafe" style={{ filter: `drop-shadow(0 0 3px ${color})` }} />
        ) : null,
      )}
    </svg>
  );
}
