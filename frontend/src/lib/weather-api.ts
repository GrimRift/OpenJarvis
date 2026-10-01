/** Settings > Weather (M41), kept on the server in connectors/weather.json. */

import { apiFetch } from './api';

export interface WeatherPlace {
  name: string;
  admin1?: string;
  admin2?: string;
  country?: string;
  country_code?: string;
  latitude: number;
  longitude: number;
  timezone?: string;
  label?: string;
}

export interface WeatherSettings {
  place: WeatherPlace | null;
  place_label: string;
  /** A city name from before M41, not yet pinned to coordinates. */
  legacy_location: string;
  use_device_location: boolean;
  units: string;
  wind_unit: string;
  units_options: string[];
  wind_unit_options: string[];
  source: string;
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export async function fetchWeatherSettings(): Promise<WeatherSettings> {
  return json(await apiFetch('/v1/weather/settings'));
}

export async function saveWeatherSettings(
  changes: Partial<Pick<WeatherSettings, 'use_device_location' | 'units' | 'wind_unit'>> & {
    place?: WeatherPlace;
  },
): Promise<WeatherSettings> {
  return json(
    await apiFetch('/v1/weather/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    }),
  );
}

export async function searchWeatherPlaces(query: string): Promise<WeatherPlace[]> {
  const body = await json<{ results: WeatherPlace[] }>(
    await apiFetch(`/v1/weather/places?q=${encodeURIComponent(query)}`),
  );
  return body.results ?? [];
}
