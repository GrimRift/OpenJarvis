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
  cloudAvailable: true,
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

  it('falls back to local when the cloud provider has no key', () => {
    // Preferring cloud must never leave Sage with nothing to answer on.
    expect(
      preferredModelId(
        [DEFAULT_LOCAL_MODEL],
        pref({ cloudAvailable: false }),
      ),
    ).toBe(DEFAULT_LOCAL_MODEL);
  });

  it('picks cloud even though /v1/models never lists it', () => {
    // The regression this whole change exists for: direct cloud models are
    // filtered out of /v1/models by design, so requiring list membership fell
    // back to qwen3.5:4b on every load.
    expect(preferredModelId([DEFAULT_LOCAL_MODEL], pref())).toBe(
      DEFAULT_CLOUD_MODEL,
    );
  });

  it('falls back to whatever exists when neither named model is present', () => {
    expect(
      preferredModelId(['some-other-model'], pref({ preferCloudModel: false })),
    ).toBe('some-other-model');
  });

  it('returns empty when the server offers nothing', () => {
    expect(preferredModelId([], pref({ preferCloudModel: false }))).toBe('');
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

  it('does not switch to cloud when there is no key', () => {
    // Toggling on without a key should leave a working model selected.
    expect(
      modelForToggle(
        [DEFAULT_LOCAL_MODEL],
        pref({ cloudAvailable: false }),
        true,
      ),
    ).toBe(DEFAULT_LOCAL_MODEL);
  });
});
