import { describe, expect, it } from 'vitest';
import { DEFAULT_VOLUMES, effectiveVolume, normaliseVolumes } from './volume';

describe('volumes', () => {
  it('effective is master times channel, clamped', () => {
    expect(effectiveVolume({ ...DEFAULT_VOLUMES, master: 0.5, chat: 0.8 }, 'chat')).toBeCloseTo(0.4);
    expect(effectiveVolume({ ...DEFAULT_VOLUMES, master: 0 }, 'chime')).toBe(0);
    expect(effectiveVolume(DEFAULT_VOLUMES, 'ack')).toBe(1);
  });

  it('normalises whatever the server or a bad payload sends', () => {
    expect(normaliseVolumes(null)).toEqual(DEFAULT_VOLUMES);
    expect(normaliseVolumes({ master: 0.3, chat: 'loud', chime: 4, ack: -1 })).toEqual({
      ...DEFAULT_VOLUMES,
      master: 0.3,
      chime: 1,
      ack: 0,
    });
  });
});
