import { describe, expect, it } from 'vitest';
import {
  DEFAULT_CLOUD_MODEL,
  DEFAULT_LOCAL_MODEL,
  modelForToggle,
  preferredModelId,
} from './model-preference';

const BOTH = [DEFAULT_LOCAL_MODEL, DEFAULT_CLOUD_MODEL];
const pref = (over: Partial<Parameters<typeof preferredModelId>[1]> = {}) => ({
  preferCloudModel: true,
  cloudModel: DEFAULT_CLOUD_MODEL,
  localModel: DEFAULT_LOCAL_MODEL,
  ...over,
});

describe('preferredModelId', () => {
  it('picks cloud when preferred and available', () => {
    expect(preferredModelId(BOTH, pref())).toBe(DEFAULT_CLOUD_MODEL);
  });

  it('picks local when cloud is not preferred', () => {
    expect(preferredModelId(BOTH, pref({ preferCloudModel: false }))).toBe(
      DEFAULT_LOCAL_MODEL,
    );
  });

  it('falls back to local when cloud is missing', () => {
    // No API key, no credit, or offline. Preferring cloud must never leave
    // Sage with nothing to answer on.
    expect(preferredModelId([DEFAULT_LOCAL_MODEL], pref())).toBe(
      DEFAULT_LOCAL_MODEL,
    );
  });

  it('falls back to whatever exists when neither named model is present', () => {
    expect(preferredModelId(['some-other-model'], pref())).toBe(
      'some-other-model',
    );
  });

  it('returns empty when the server offers nothing', () => {
    expect(preferredModelId([], pref())).toBe('');
  });

  it('ignores an empty model id rather than selecting it', () => {
    expect(preferredModelId(BOTH, pref({ cloudModel: '' }))).toBe(
      DEFAULT_LOCAL_MODEL,
    );
  });
});

describe('modelForToggle', () => {
  it('switches to cloud when turned on', () => {
    expect(modelForToggle(BOTH, pref({ preferCloudModel: false }), true)).toBe(
      DEFAULT_CLOUD_MODEL,
    );
  });

  it('switches to local when turned off', () => {
    expect(modelForToggle(BOTH, pref(), false)).toBe(DEFAULT_LOCAL_MODEL);
  });

  it('does not switch to a cloud model the server is not offering', () => {
    // Toggling on while offline should leave a working model selected.
    expect(modelForToggle([DEFAULT_LOCAL_MODEL], pref(), true)).toBe(
      DEFAULT_LOCAL_MODEL,
    );
  });
});
