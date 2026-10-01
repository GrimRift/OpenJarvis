import { describe, expect, it } from 'vitest';
import { isCloseDiagramCommand } from './diagram';
import { useWeatherPresenter } from './weather-presenter';
import {
  clockLabel,
  compass,
  dayLabel,
  hourLabel,
  observedLabel,
  weatherFromToolCall,
  weatherIn,
} from './weather-report';

const REPORT = {
  place: 'Calamba, Laguna',
  observed_at: '2026-10-01T17:15',
  timezone: 'Asia/Manila',
  temp_unit: '°C',
  speed_unit: 'km/h',
  source: 'Open-Meteo',
  summary: '27°C, drizzle — rain likely around 5 PM (90%)',
  current: { temp: 27.1, conditions: 'drizzle', icon: 'drizzle', is_day: true },
  hourly: [],
  daily: [],
};

function call(id: string, metadata: Record<string, unknown> | undefined, tool = 'weather') {
  return { id, tool, arguments: '{}', status: 'success' as const, metadata };
}

describe('weather report from a tool call', () => {
  it('reads the panel data and keys it by the call', () => {
    const report = weatherFromToolCall(call('tc1', { weather: REPORT }));
    expect(report?.place).toBe('Calamba, Laguna');
    expect(report?.key).toBe('tc1');
  });

  it('ignores other tools, failures and calls without a report', () => {
    expect(weatherFromToolCall(call('a', { weather: REPORT }, 'web_search'))).toBeNull();
    expect(weatherFromToolCall({ ...call('b', { weather: REPORT }), status: 'error' })).toBeNull();
    // A reply persisted before M41 carries only the summary fields.
    expect(weatherFromToolCall(call('c', { summary: 'x' }))).toBeNull();
  });

  it('a reply that asked twice shows the newer forecast', () => {
    const later = { ...REPORT, place: 'Tokyo' };
    expect(weatherIn([call('1', { weather: REPORT }), call('2', { weather: later })])?.place).toBe('Tokyo');
  });
});

describe('labels read the place’s own clock, not this PC’s', () => {
  it('formats hours and clock times from the text', () => {
    expect(hourLabel('2026-10-01T00:00')).toBe('12 AM');
    expect(hourLabel('2026-10-01T17:00')).toBe('5 PM');
    expect(clockLabel('2026-10-01T05:44')).toBe('5:44 AM');
    expect(clockLabel(null)).toBe('—');
    expect(observedLabel('2026-10-01T17:15')).toBe('Thu 1 Oct, 5:15 PM');
  });

  it('names days, with the first as today', () => {
    expect(dayLabel('2026-10-01', 0)).toBe('Today');
    expect(dayLabel('2026-10-02', 1)).toBe('Fri');
  });

  it('turns degrees into a compass point', () => {
    expect(compass(276)).toBe('W');
    expect(compass(44)).toBe('NE');
    expect(compass(359)).toBe('N');
    expect(compass(null)).toBe('');
  });
});

describe('the panel opens once per answer', () => {
  it('auto-opens a new report, never the same one twice', () => {
    const report = weatherFromToolCall(call('once', { weather: REPORT }))!;
    const presenter = useWeatherPresenter.getState();
    expect(presenter.showNew(report, true)).toBe(true);
    useWeatherPresenter.getState().close();
    expect(useWeatherPresenter.getState().showNew(report, true)).toBe(false);
    expect(useWeatherPresenter.getState().current).toBeNull();
  });

  it('with auto-open off it is only marked seen', () => {
    const report = weatherFromToolCall(call('off', { weather: REPORT }))!;
    expect(useWeatherPresenter.getState().showNew(report, false)).toBe(false);
    expect(useWeatherPresenter.getState().current).toBeNull();
  });
});

describe('closing the panel by voice', () => {
  it.each(['close the weather', 'close the forecast', 'hide the panel', 'Cost the weather.'])(
    '%s',
    (said) => {
      expect(isCloseDiagramCommand(said)).toBe(true);
    },
  );

  it('does not catch ordinary weather questions', () => {
    expect(isCloseDiagramCommand('what is the weather tomorrow')).toBe(false);
    expect(isCloseDiagramCommand('will it rain close to noon')).toBe(false);
  });
});
