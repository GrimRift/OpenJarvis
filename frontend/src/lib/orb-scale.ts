// The chat orb keeps its proportion to the window (user, 29 September): at
// the window the app first opens with (1419 x 866 inside) it is its full
// 473 px, smaller windows get a proportionally smaller orb, larger ones never
// a bigger one. Text is not scaled -- only the orb.
import { useEffect, useState } from 'react';

export const ORB_REFERENCE_WIDTH = 1419;
export const ORB_REFERENCE_HEIGHT = 866;
/** Below this the orb would stop reading as the orb. */
const MIN_SCALE = 0.3;

export function orbScaleFor(width: number, height: number): number {
  if (!(width > 0) || !(height > 0)) return 1;
  const scale = Math.min(1, width / ORB_REFERENCE_WIDTH, height / ORB_REFERENCE_HEIGHT);
  return Math.max(MIN_SCALE, scale);
}

export function useOrbScale(): number {
  const read = () => (typeof window === 'undefined' ? 1 : orbScaleFor(window.innerWidth, window.innerHeight));
  const [scale, setScale] = useState(read);
  useEffect(() => {
    const onResize = () => setScale(read());
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return scale;
}
