import { describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({ apiFetch: vi.fn(() => Promise.resolve()) }));

import { holdMedia, mediaHeld } from './media-hold';

describe('mediaHeld', () => {
  it('follows the last hold the page asked for', () => {
    expect(mediaHeld()).toBe(false);
    holdMedia('duck');
    expect(mediaHeld()).toBe(true);
    holdMedia('release');
    expect(mediaHeld()).toBe(false);
  });
});
