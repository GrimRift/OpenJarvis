/**
 * The weather panel's data (M41), as it arrives on a `weather` tool call.
 *
 * The server puts the whole report in `metadata.weather` (Open-Meteo, already
 * labelled and in the user's units). Tool calls are persisted with the
 * conversation, so the chat card can reopen the panel after a reload.
 * Times are the place's own local time ("2026-10-01T17:00"), so they are
 * read as text, never through `Date`, which would shift them to this PC's zone.
 */

import type { ToolCallInfo } from '../types';

export type WeatherIcon =
  | 'clear'
  | 'partly-cloudy'
  | 'cloudy'
  | 'fog'
  | 'drizzle'
  | 'rain'
  | 'showers'
  | 'snow'
  | 'thunder';

export interface WeatherHour {
  time: string;
  temp: number | null;
  rain_chance: number | null;
  icon: WeatherIcon;
  is_day: boolean;
}

export interface WeatherDay {
  date: string;
  conditions: string;
  icon: WeatherIcon;
  high: number | null;
  low: number | null;
  rain_chance: number | null;
  uv_max: number | null;
  sunrise: string | null;
  sunset: string | null;
  /** That day's own hours (M41 design pass); absent on reports saved before it. */
  hours?: WeatherHour[];
  /** One line for the day, shown under "Sage". */
  advice?: string;
}

export interface WeatherReport {
  /** Stable per tool call: what "seen" is keyed by. */
  key: string;
  place: string;
  observed_at: string;
  timezone: string;
  temp_unit: string;
  speed_unit: string;
  source: string;
  summary: string;
  /** Index into `daily` of the day the user asked about ("Friday"), if any. */
  focus_day?: number | null;
  current: {
    temp: number | null;
    feels_like: number | null;
    humidity: number | null;
    wind_speed: number | null;
    wind_direction: number | null;
    pressure: number | null;
    uv_index: number | null;
    is_day: boolean;
    conditions: string;
    icon: WeatherIcon;
  };
  hourly: WeatherHour[];
  daily: WeatherDay[];
}

export const WEATHER_TOOL = 'weather';

/** The report a finished weather call carried, if it carried one. */
export function weatherFromToolCall(
  call: Pick<ToolCallInfo, 'id' | 'tool' | 'status' | 'metadata'>,
): WeatherReport | null {
  if (call.tool !== WEATHER_TOOL || call.status !== 'success') return null;
  const raw = call.metadata?.weather;
  if (!raw || typeof raw !== 'object') return null;
  const report = raw as Omit<WeatherReport, 'key'>;
  if (!report.current || !Array.isArray(report.daily) || !Array.isArray(report.hourly)) return null;
  return { ...report, key: call.id || `${report.place}:${report.observed_at}` };
}

/** The last forecast a reply fetched (asking twice shows the newer one). */
export function weatherIn(toolCalls: ToolCallInfo[] | undefined): WeatherReport | null {
  let found: WeatherReport | null = null;
  for (const call of toolCalls ?? []) found = weatherFromToolCall(call) ?? found;
  return found;
}

/** "5 PM" from "2026-10-01T17:00". */
export function hourLabel(stamp: string | null | undefined): string {
  const match = /T(\d{2}):(\d{2})/.exec(stamp ?? '');
  if (!match) return '';
  const hour = Number(match[1]);
  const suffix = hour < 12 ? 'AM' : 'PM';
  return `${hour % 12 === 0 ? 12 : hour % 12} ${suffix}`;
}

/** "5:44 AM" from "2026-10-01T05:44". */
export function clockLabel(stamp: string | null | undefined): string {
  const match = /T(\d{2}):(\d{2})/.exec(stamp ?? '');
  if (!match) return '—';
  const hour = Number(match[1]);
  return `${hour % 12 === 0 ? 12 : hour % 12}:${match[2]} ${hour < 12 ? 'AM' : 'PM'}`;
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "Thu" (or "Today") from "2026-10-01". */
export function dayLabel(date: string, index: number): string {
  if (index === 0) return 'Today';
  const [y, m, d] = date.split('-').map(Number);
  if (!y || !m || !d) return date;
  return WEEKDAYS[new Date(y, m - 1, d).getDay()];
}

/** "Thu 1 Oct, 5:15 PM" from "2026-10-01T17:15". */
export function observedLabel(stamp: string): string {
  const [day] = stamp.split('T');
  const [y, m, d] = day.split('-').map(Number);
  if (!y || !m || !d) return stamp;
  const weekday = WEEKDAYS[new Date(y, m - 1, d).getDay()];
  return `${weekday} ${d} ${MONTHS[m - 1]}, ${clockLabel(stamp)}`;
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

export function compass(degrees: number | null | undefined): string {
  if (typeof degrees !== 'number' || !Number.isFinite(degrees)) return '';
  return COMPASS[Math.round((((degrees % 360) + 360) % 360) / 45) % 8];
}

export function round(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value)) : '—';
}

export function percent(chance: number | null | undefined): string {
  return typeof chance === 'number' && Number.isFinite(chance) ? `${Math.round(chance * 100)}%` : '—';
}
