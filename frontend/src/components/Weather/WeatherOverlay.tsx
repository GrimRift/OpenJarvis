/**
 * The weather panel (M41), in the design the user picked on 2 Oct ("2C" +
 * "S3"): the week as a list on the left, the chosen day on the right with
 * Sage's line about it and that day's own hours, over particles that act out
 * the weather. The hourly curve is drawn as linked, glowing points and the
 * chosen day breathes.
 *
 * It opens on the day the user asked about ("Friday's weather"), otherwise on
 * Now. It stays until the user closes it -- Esc, Close, a click outside, or
 * "close it" by voice -- and does not leave with the voice.
 */

import { useEffect, useMemo, useState } from 'react';
import { Umbrella } from 'lucide-react';
import { isBackdropClick } from '../../lib/overlay-backdrop';
import {
  clockLabel,
  compass,
  dayLabel,
  hourLabel,
  observedLabel,
  percent,
  round,
  type WeatherDay,
  type WeatherHour,
  type WeatherReport,
} from '../../lib/weather-report';
import { WeatherIcon } from './WeatherIcon';
import { WeatherParticles } from './WeatherParticles';

const ACCENT = '#22d3ee';
const MUTED = '#a1a1aa';
const DIM = '#71717a';
const CARD = 'rgba(22, 22, 26, 0.82)';

const STYLE = `
@keyframes wx-breathe {
  0%, 100% { box-shadow: inset 2px 0 0 ${ACCENT}, 0 0 10px rgba(34,211,238,0.10); }
  50% { box-shadow: inset 2px 0 0 ${ACCENT}, 0 0 26px rgba(34,211,238,0.32); }
}
.wx-day { transition: background 0.2s; }
.wx-day:hover { background: rgba(255,255,255,0.04); }
.wx-day[aria-pressed='true'] { background: rgba(34,211,238,0.09); animation: wx-breathe 2.6s ease-in-out infinite; }
@media (prefers-reduced-motion: reduce) { .wx-day[aria-pressed='true'] { animation: none; box-shadow: inset 2px 0 0 ${ACCENT}; } }
`;

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

function longDate(date: string): string {
  const [y, m, d] = date.split('-').map(Number);
  if (!y || !m || !d) return date;
  return `${WEEKDAYS[new Date(y, m - 1, d).getDay()]} ${d} ${MONTHS[m - 1]}`;
}

/** The day the question was about, if it was a later day (today = Now). */
function focusDay(report: WeatherReport): number | null {
  const i = report.focus_day;
  return typeof i === 'number' && i > 0 && i < report.daily.length ? i : null;
}

/** A day's own hours; reports saved before they existed fall back to the next 24. */
function hoursOf(report: WeatherReport, day: WeatherDay | null): WeatherHour[] {
  if (!day) return report.hourly;
  if (day.hours?.length) return day.hours;
  return report.hourly.filter((h) => h.time.startsWith(day.date));
}

/** Temperature through the hours as linked, glowing points; rain chance as bars. */
function ConstellationCurve({ hours, label }: { hours: WeatherHour[]; label: string }) {
  const points = hours.filter((h) => typeof h.temp === 'number');
  if (points.length < 2) return null;
  const width = 560;
  const height = 150;
  const temps = points.map((h) => h.temp as number);
  const min = Math.min(...temps);
  const span = Math.max(1, Math.max(...temps) - min);
  const step = width / points.length;
  const x = (i: number) => step * i + step / 2;
  const y = (t: number) => height - 36 - ((t - min) / span) * (height - 68);
  const line = points.map((h, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(h.temp as number).toFixed(1)}`).join(' ');

  return (
    <div>
      <div style={{ fontSize: 11, letterSpacing: '0.14em', fontWeight: 600, color: MUTED, textTransform: 'uppercase', margin: '14px 0 4px' }}>
        {label}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={`${label}: temperature and rain chance`}>
        <defs>
          <filter id="wx-glow" x="-20%" y="-50%" width="140%" height="200%">
            <feGaussianBlur stdDeviation="3" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        {points.slice(0, -2).map((h, i) => (
          <line
            key={`x${h.time}`}
            x1={x(i)}
            y1={y(h.temp as number)}
            x2={x(i + 2)}
            y2={y(points[i + 2].temp as number)}
            stroke={ACCENT}
            strokeOpacity={0.12}
          />
        ))}
        <path d={line} fill="none" stroke={ACCENT} strokeOpacity={0.7} strokeWidth={1.4} filter="url(#wx-glow)" />
        {points.map((h, i) => {
          const rain = typeof h.rain_chance === 'number' ? h.rain_chance : 0;
          const labelled = i % 3 === 0;
          return (
            <g key={h.time}>
              <rect x={x(i) - 4} y={height - 20 - rain * 16} width={8} height={Math.max(1.5, rain * 16)} rx={2} fill="#38bdf8" opacity={0.2 + rain * 0.65}>
                <title>{`${hourLabel(h.time)}: ${round(h.temp)}°, rain ${percent(h.rain_chance)}`}</title>
              </rect>
              <circle cx={x(i)} cy={y(h.temp as number)} r={labelled ? 3.2 : 1.8} fill="#cffafe" filter="url(#wx-glow)" />
              {labelled ? (
                <>
                  <text x={x(i)} y={y(h.temp as number) - 10} textAnchor="middle" fontSize={12} fill="#e4e4e7">
                    {round(h.temp)}°
                  </text>
                  <text x={x(i)} y={height - 3} textAnchor={i ? 'middle' : 'start'} fontSize={10.5} fill={DIM}>
                    {hourLabel(h.time)}
                  </text>
                </>
              ) : null}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: CARD, borderRadius: 10, padding: '8px 10px', border: '1px solid rgba(255,255,255,0.05)', minWidth: 0 }}>
      <div style={{ fontSize: 11.5, color: DIM }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: '#fafafa', whiteSpace: 'nowrap' }}>{value}</div>
    </div>
  );
}

interface Props {
  report: WeatherReport;
  onClose: () => void;
}

export function WeatherOverlay({ report, onClose }: Props) {
  // null = Now (today's live conditions); a number = the day picked in the list.
  const [picked, setPicked] = useState<number | null>(() => focusDay(report));

  useEffect(() => setPicked(focusDay(report)), [report]);

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
  const day = picked === null ? null : report.daily[picked] ?? null;
  const title = report.place || 'Your location';
  const hours = hoursOf(report, day);

  const advice = day ? day.advice || `${day.conditions}, rain ${percent(day.rain_chance)}.` : report.summary;
  const stats = useMemo(
    () =>
      day
        ? [
            { label: 'Rain', value: percent(day.rain_chance) },
            { label: 'UV max', value: round(day.uv_max) },
            { label: 'Sunrise', value: clockLabel(day.sunrise) },
            { label: 'Sunset', value: clockLabel(day.sunset) },
          ]
        : [
            { label: 'Humidity', value: `${round(now.humidity)}%` },
            { label: 'Wind', value: `${round(now.wind_speed)} ${report.speed_unit} ${compass(now.wind_direction)}`.trim() },
            { label: 'Pressure', value: `${round(now.pressure)} hPa` },
            // After today's sunset the next thing worth knowing is sunrise.
            report.observed_at && today?.sunset && report.observed_at > today.sunset
              ? { label: 'Sunrise', value: clockLabel(report.daily[1]?.sunrise) }
              : { label: 'Sunset', value: clockLabel(today?.sunset) },
          ],
    [day, now, today, report.speed_unit, report.observed_at, report.daily],
  );
  const heavy = (day ? day.rain_chance : hours[0]?.rain_chance ?? 0) ?? 0;

  const tabStyle = (on: boolean): React.CSSProperties => ({
    fontSize: 12.5,
    padding: '5px 12px',
    borderRadius: 8,
    border: 'none',
    cursor: 'pointer',
    color: on ? '#fafafa' : MUTED,
    background: on ? 'rgba(34,211,238,0.14)' : 'transparent',
    boxShadow: on ? 'inset 0 0 12px rgba(34,211,238,0.25)' : 'none',
  });

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80 }} role="presentation">
      <style>{STYLE}</style>
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
            position: 'relative',
            width: 'min(920px, 100%)',
            borderRadius: 18,
            background: '#0f1013',
            border: '1px solid rgba(34,211,238,0.22)',
            boxShadow: '0 24px 70px rgba(0,0,0,0.6), 0 0 40px rgba(34,211,238,0.08)',
            overflow: 'hidden',
            color: '#fafafa',
          }}
        >
          <WeatherParticles icon={day ? day.icon : now.icon} isDay={day ? true : now.is_day} heavy={heavy >= 0.8} />
          <div
            aria-hidden
            style={{
              position: 'absolute',
              inset: 0,
              borderRadius: 18,
              pointerEvents: 'none',
              boxShadow: 'inset 0 0 0 1px rgba(34,211,238,0.22), inset 0 0 50px rgba(34,211,238,0.08), inset 0 -40px 80px rgba(183,148,255,0.05)',
            }}
          />

          <div style={{ position: 'relative', padding: '20px 24px 16px' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 17, fontWeight: 600 }}>{title}</span>
              <span style={{ fontSize: 12, color: DIM }}>
                {report.observed_at ? observedLabel(report.observed_at) : ''} · {report.source}
              </span>
              <div
                style={{
                  marginLeft: 'auto',
                  display: 'flex',
                  gap: 2,
                  background: 'rgba(22,22,26,0.9)',
                  padding: 3,
                  borderRadius: 10,
                  border: '1px solid rgba(255,255,255,0.06)',
                }}
              >
                <button type="button" style={tabStyle(picked === null)} onClick={() => setPicked(null)}>
                  Now {round(now.temp)}°
                </button>
                {day ? (
                  <button type="button" style={tabStyle(true)}>
                    {longDate(day.date).split(' ')[0]}
                  </button>
                ) : null}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(200px, 240px) 1fr', gap: 18, marginTop: 16 }}>
              {/* The week */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {report.daily.map((d, i) => {
                  const selected = i === 0 ? picked === null : picked === i;
                  return (
                    <button
                      key={d.date}
                      type="button"
                      className="wx-day"
                      aria-pressed={selected}
                      onClick={() => setPicked(i === 0 ? null : i)}
                      title={d.advice || d.conditions}
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '44px 22px 1fr auto',
                        gap: 8,
                        alignItems: 'center',
                        padding: '9px 10px',
                        borderRadius: 10,
                        border: 'none',
                        background: 'transparent',
                        color: '#fafafa',
                        fontSize: 13,
                        textAlign: 'left',
                        cursor: 'pointer',
                      }}
                    >
                      <span>{dayLabel(d.date, i)}</span>
                      <WeatherIcon kind={d.icon} size={20} />
                      <span style={{ color: '#38bdf8', fontSize: 11.5 }}>{percent(d.rain_chance)}</span>
                      <span style={{ whiteSpace: 'nowrap' }}>
                        {round(d.high)}° <span style={{ color: DIM }}>{round(d.low)}°</span>
                      </span>
                    </button>
                  );
                })}
              </div>

              {/* The chosen day (or Now) */}
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 16, flexWrap: 'wrap' }}>
                  <span style={{ filter: 'drop-shadow(0 0 10px rgba(34,211,238,0.55))', display: 'flex' }}>
                    <WeatherIcon kind={day ? day.icon : now.icon} day={day ? true : now.is_day} size={58} />
                  </span>
                  <span style={{ fontSize: 58, fontWeight: 600, lineHeight: 1, textShadow: '0 0 18px rgba(34,211,238,0.35)' }}>
                    {day ? round(day.high) : round(now.temp)}°
                    <span style={{ fontSize: 18, color: MUTED, fontWeight: 500, marginLeft: 2 }}>{unit.replace('°', '')}</span>
                  </span>
                  <div style={{ paddingBottom: 6 }}>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>{day ? longDate(day.date) : 'Now'}</div>
                    <div style={{ color: MUTED }}>
                      <span style={{ textTransform: 'capitalize' }}>{day ? day.conditions : now.conditions}</span>
                      {day ? ` · low ${round(day.low)}°` : ` · feels like ${round(now.feels_like)}°`}
                    </div>
                  </div>
                </div>

                {advice ? (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 10,
                      fontSize: 13.5,
                      background: CARD,
                      borderRadius: 12,
                      padding: '10px 14px',
                      marginTop: 14,
                      border: '1px solid rgba(34,211,238,0.18)',
                    }}
                  >
                    <Umbrella size={18} color={ACCENT} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden />
                    <div>
                      <div style={{ fontSize: 10, letterSpacing: '0.32em', color: ACCENT, textShadow: '0 0 8px rgba(34,211,238,0.3)', marginBottom: 2 }}>
                        SAGE
                      </div>
                      {advice}
                    </div>
                  </div>
                ) : null}

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8, marginTop: 12 }}>
                  {stats.map((s) => (
                    <Stat key={s.label} label={s.label} value={s.value} />
                  ))}
                </div>

                <ConstellationCurve hours={hours} label={day ? `${longDate(day.date).split(' ')[0]}, hour by hour` : 'Next 24 hours'} />

                {day ? (
                  <div style={{ fontSize: 12, color: DIM }}>
                    Now: {round(now.temp)}°, {now.conditions} · humidity {round(now.humidity)}% · wind {round(now.wind_speed)} {report.speed_unit}{' '}
                    {compass(now.wind_direction)}
                  </div>
                ) : null}
              </div>
            </div>

            <div style={{ fontSize: 11.5, color: DIM, marginTop: 10, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
              <span>{report.timezone}</span>
              <span>Esc, Close or “close it” · click a day to see it</span>
            </div>
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
