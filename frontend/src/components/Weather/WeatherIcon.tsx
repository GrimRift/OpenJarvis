import {
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudMoon,
  CloudRain,
  CloudSnow,
  CloudSun,
  CloudSunRain,
  Moon,
  Sun,
} from 'lucide-react';
import type { WeatherIcon as Kind } from '../../lib/weather-report';

const COLORS: Record<Kind, string> = {
  clear: '#fbbf24',
  'partly-cloudy': '#fcd34d',
  cloudy: '#cbd5e1',
  fog: '#94a3b8',
  drizzle: '#7dd3fc',
  rain: '#38bdf8',
  showers: '#38bdf8',
  snow: '#e0f2fe',
  thunder: '#a78bfa',
};

export function WeatherIcon({ kind, day = true, size = 24 }: { kind: Kind; day?: boolean; size?: number }) {
  const props = { size, color: COLORS[kind] ?? '#cbd5e1', strokeWidth: 1.6, 'aria-hidden': true };
  switch (kind) {
    case 'clear':
      return day ? <Sun {...props} /> : <Moon {...props} color="#e2e8f0" />;
    case 'partly-cloudy':
      return day ? <CloudSun {...props} /> : <CloudMoon {...props} color="#e2e8f0" />;
    case 'fog':
      return <CloudFog {...props} />;
    case 'drizzle':
      return <CloudDrizzle {...props} />;
    case 'rain':
      return <CloudRain {...props} />;
    case 'showers':
      return day ? <CloudSunRain {...props} /> : <CloudRain {...props} />;
    case 'snow':
      return <CloudSnow {...props} />;
    case 'thunder':
      return <CloudLightning {...props} />;
    default:
      return <Cloud {...props} />;
  }
}
