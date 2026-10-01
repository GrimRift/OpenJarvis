/**
 * The weather panel (M41): today's conditions, a 24-hour curve and seven days,
 * over the whole app.
 *
 * Styled like the image overlay (the app behind is dimmed). It stays until the
 * user closes it -- Esc, Close, a click outside, or "close it" by voice -- and
 * does not leave with the voice: Sage says one line, the week is to be read.
 * Clicking a day shows that day's figures in the stats block.
 */

import { useEffect, useMemo, useState } from 'react';
import { Droplets, Gauge, Navigation, Sun, Sunrise, Sunset, Thermometer, Wind } from 'lucide-react';
import { isBackdropClick } from '../../lib/overlay-backdrop';
import {
  clockLabel,
  compass,
  dayLabel,
  hourLabel,
  observedLabel,
  percent,
  round,
  type WeatherReport,
} from '../../lib/weather-report';
import { WeatherIcon } from './WeatherIcon';

const ACCENT = '#38bdf8';
const MUTED = '#94a3b8';
const PANEL_BG = 'linear-gradient(160deg, rgba(15, 23, 42, 0.96), rgba(8, 47, 73, 0.94))';

interface Props {
  report: WeatherReport;
  onClose: () => void;
}

function Stat({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
      <span style={{ color: MUTED, display: 'flex' }}>{icon}</span>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: MUTED }}>{label}</div>
        <div style={{ fontSize: 15, fontWeight: 600, color: '#f1f5f9', whiteSpace: 'nowrap' }}>{value}</div>
      </div>
    </div>
  );
}

/** Temperature over the next 24 hours, with each hour's rain chance as a bar. */
function HourlyCurve({ report }: { report: WeatherReport }) {
  const hours = report.hourly.filter((h) => typeof h.temp === 'number');
  if (hours.length < 2) return null;
  const width = 960;
  const height = 150;
  const top = 34;
  const curveBottom = 92;
  const temps = hours.map((h) => h.temp as number);
  const min = Math.min(...temps);
  const max = Math.max(...temps);
  const span = Math.max(1, max - min);
  const step = width / hours.length;
  const x = (i: number) => step * i + step / 2;
  const y = (t: number) => curveBottom - ((t - min) / span) * (curveBottom - top);
  const line = hours.map((h, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(h.temp as number).toFixed(1)}`).join(' ');
  const area = `${line} L${x(hours.length - 1).toFixed(1)},${curveBottom + 6} L${x(0).toFixed(1)},${curveBottom + 6} Z`;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label="Temperature and rain chance for the next 24 hours">
      <defs>
        <linearGradient id="wx-area" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={ACCENT} stopOpacity="0.35" />
          <stop offset="100%" stopColor={ACCENT} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#wx-area)" />
      <path d={line} fill="none" stroke={ACCENT} strokeWidth={2.5} strokeLinejoin="round" />
      {hours.map((h, i) => {
        const rain = typeof h.rain_chance === 'number' ? h.rain_chance : 0;
        const labelled = i % 3 === 0;
        return (
          <g key={h.time}>
            <rect
              x={x(i) - step * 0.3}
              y={height - 20 - rain * 26}
              width={step * 0.6}
              height={Math.max(1, rain * 26)}
              rx={2}
              fill="#60a5fa"
              opacity={0.25 + rain * 0.6}
            >
              <title>{`${hourLabel(h.time)}: rain ${percent(h.rain_chance)}`}</title>
            </rect>
            {labelled ? (
              <>
                <circle cx={x(i)} cy={y(h.temp as number)} r={3.5} fill="#0f172a" stroke={ACCENT} strokeWidth={2} />
                <text x={x(i)} y={y(h.temp as number) - 10} textAnchor="middle" fontSize={13} fontWeight={600} fill="#e2e8f0">
                  {round(h.temp)}°
                </text>
                <text x={x(i)} y={height - 4} textAnchor="middle" fontSize={11} fill={MUTED}>
                  {i === 0 ? 'Now' : hourLabel(h.time)}
                </text>
              </>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

export function WeatherOverlay({ report, onClose }: Props) {
  // null = today's live conditions; a number = the day picked in the row.
  const [picked, setPicked] = useState<number | null>(null);

  useEffect(() => setPicked(null), [report.key]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const unit = report.temp_unit;
  const now = report.current;
  const today = report.daily[0];
  const day = picked === null ? null : report.daily[picked];
  const title = report.place || 'Your location';

  const stats = useMemo(() => {
    if (day) {
      return [
        { icon: <Thermometer size={18} />, label: 'High / Low', value: `${round(day.high)}° / ${round(day.low)}°` },
        { icon: <Droplets size={18} />, label: 'Rain chance', value: percent(day.rain_chance) },
        { icon: <Sun size={18} />, label: 'UV max', value: round(day.uv_max) },
        { icon: <Sunrise size={18} />, label: 'Sunrise', value: clockLabel(day.sunrise) },
        { icon: <Sunset size={18} />, label: 'Sunset', value: clockLabel(day.sunset) },
      ];
    }
    return [
      { icon: <Droplets size={18} />, label: 'Humidity', value: `${round(now.humidity)}%` },
      {
        icon: <Wind size={18} />,
        label: 'Wind',
        value: `${round(now.wind_speed)} ${report.speed_unit} ${compass(now.wind_direction)}`.trim(),
      },
      { icon: <Gauge size={18} />, label: 'Pressure', value: `${round(now.pressure)} hPa` },
      { icon: <Sun size={18} />, label: 'UV index', value: round(now.uv_index) },
      { icon: <Sunrise size={18} />, label: 'Sunrise', value: clockLabel(today?.sunrise) },
      { icon: <Sunset size={18} />, label: 'Sunset', value: clockLabel(today?.sunset) },
    ];
  }, [day, now, today, report.speed_unit]);

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80 }} role="presentation">
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(10, 10, 11, 0.62)', backdropFilter: 'blur(2px)' }} />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          boxSizing: 'border-box',
          padding: '56px 24px 28px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'auto',
        }}
        onClick={(event) => {
          if (isBackdropClick(event)) onClose();
        }}
        role="dialog"
        aria-modal="true"
        aria-label={`Weather for ${title}`}
      >
        <div
          style={{
            width: 'min(980px, 100%)',
            borderRadius: 18,
            border: '1px solid rgba(56, 189, 248, 0.35)',
            background: PANEL_BG,
            boxShadow: '0 24px 70px rgba(0,0,0,0.55), inset 0 0 40px rgba(56, 189, 248, 0.06)',
            color: '#f1f5f9',
            padding: '22px 26px 20px',
            display: 'flex',
            flexDirection: 'column',
            gap: 18,
          }}
        >
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: '0.14em', color: ACCENT }}>WEATHER</div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>{title}</div>
            <div style={{ fontSize: 12, color: MUTED }}>
              {report.observed_at ? observedLabel(report.observed_at) : ''}
              {report.timezone ? ` · ${report.timezone}` : ''} · {report.source}
            </div>
          </div>

          {/* Now (or the picked day) + stats */}
          <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 18, minWidth: 260 }}>
              <WeatherIcon kind={day ? day.icon : now.icon} day={day ? true : now.is_day} size={76} />
              <div>
                <div style={{ fontSize: 56, fontWeight: 700, lineHeight: 1 }}>
                  {day ? `${round(day.high)}°` : `${round(now.temp)}°`}
                  <span style={{ fontSize: 22, color: MUTED, fontWeight: 500, marginLeft: 4 }}>{unit.replace('°', '')}</span>
                </div>
                <div style={{ fontSize: 15, fontWeight: 600, color: ACCENT, textTransform: 'capitalize', marginTop: 6 }}>
                  {day ? day.conditions : now.conditions}
                </div>
                <div style={{ fontSize: 12.5, color: MUTED, marginTop: 2 }}>
                  {day
                    ? `${dayLabel(day.date, picked ?? 0)} · low ${round(day.low)}°`
                    : `Feels like ${round(now.feels_like)}°`}
                </div>
              </div>
            </div>
            <div
              style={{
                flex: 1,
                minWidth: 280,
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
                gap: '14px 18px',
                padding: '14px 16px',
                borderRadius: 12,
                background: 'rgba(15, 23, 42, 0.55)',
                border: '1px solid rgba(148, 163, 184, 0.15)',
              }}
            >
              {stats.map((s) => (
                <Stat key={s.label} icon={s.icon} label={s.label} value={s.value} />
              ))}
            </div>
          </div>

          {report.summary && !day ? (
            <div style={{ fontSize: 13.5, color: '#cbd5e1', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Navigation size={14} color={ACCENT} style={{ transform: 'rotate(90deg)' }} aria-hidden />
              {report.summary}
            </div>
          ) : null}

          {/* Next 24 hours */}
          <div>
            <div style={{ fontSize: 10.5, letterSpacing: '0.1em', color: MUTED, marginBottom: 4 }}>NEXT 24 HOURS</div>
            <HourlyCurve report={report} />
          </div>

          {/* Seven days */}
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.max(1, report.daily.length)}, minmax(0, 1fr))`, gap: 8 }}>
            {report.daily.map((d, i) => {
              const active = picked === i;
              return (
                <button
                  key={d.date}
                  type="button"
                  onClick={() => setPicked(active ? null : i)}
                  aria-pressed={active}
                  title={`${d.conditions}, rain ${percent(d.rain_chance)}`}
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: 5,
                    padding: '10px 4px',
                    borderRadius: 12,
                    cursor: 'pointer',
                    color: '#f1f5f9',
                    background: active ? 'rgba(56, 189, 248, 0.18)' : 'rgba(15, 23, 42, 0.5)',
                    border: `1px solid ${active ? ACCENT : 'rgba(148, 163, 184, 0.15)'}`,
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 600, color: active ? ACCENT : '#cbd5e1' }}>{dayLabel(d.date, i)}</div>
                  <WeatherIcon kind={d.icon} size={26} />
                  <div style={{ fontSize: 11, color: '#7dd3fc' }}>{percent(d.rain_chance)}</div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    {round(d.high)}° <span style={{ color: MUTED, fontWeight: 400 }}>{round(d.low)}°</span>
                  </div>
                </button>
              );
            })}
          </div>

          <div style={{ fontSize: 11, color: '#64748b', textAlign: 'center' }}>
            Esc, Close, click outside or say “close it” to dismiss · click a day for its details
          </div>
        </div>
      </div>

      <button
        type="button"
        aria-label="Close weather"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
        style={{
          position: 'absolute',
          top: 18,
          right: 20,
          padding: '8px 16px',
          borderRadius: 999,
          border: '1px solid rgba(255,255,255,0.18)',
          background: 'rgba(24,24,27,0.85)',
          color: '#fafafa',
          fontSize: 13,
          fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        Close
      </button>
    </div>
  );
}
