/**
 * What a weather answer leaves in the conversation (M41): one line that
 * reopens the panel. Rebuilt from the persisted tool call, so it survives a
 * reload; the panel is transient, this is the record.
 */

import { useWeatherPresenter } from '../../lib/weather-presenter';
import { round, type WeatherReport } from '../../lib/weather-report';
import { WeatherIcon } from './WeatherIcon';

export function WeatherCard({ report }: { report: WeatherReport }) {
  const open = useWeatherPresenter((s) => s.open);
  return (
    <button
      type="button"
      onClick={() => open(report)}
      className="my-3 flex items-center gap-3 px-3 py-2 text-left cursor-pointer"
      style={{
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--color-surface)',
        maxWidth: 420,
        width: '100%',
      }}
      aria-label={`Open the weather panel${report.place ? ` for ${report.place}` : ''}`}
    >
      <WeatherIcon kind={report.current.icon} day={report.current.is_day} size={28} />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
          {round(report.current.temp)}
          {report.temp_unit} · <span className="capitalize">{report.current.conditions}</span>
        </div>
        <div className="text-[11.5px] truncate" style={{ color: 'var(--color-text-tertiary)' }}>
          {report.place || 'Your location'} · 7-day forecast
        </div>
      </div>
      <span className="text-xs whitespace-nowrap" style={{ color: 'var(--color-accent)' }}>
        Open
      </span>
    </button>
  );
}
