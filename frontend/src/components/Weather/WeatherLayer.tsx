/** Mounts the open weather panel (M41), once, above the whole app. */

import { useWeatherPresenter } from '../../lib/weather-presenter';
import { WeatherOverlay } from './WeatherOverlay';

export function WeatherLayer() {
  const current = useWeatherPresenter((s) => s.current);
  const close = useWeatherPresenter((s) => s.close);
  if (!current) return null;
  return <WeatherOverlay report={current} onClose={close} />;
}
