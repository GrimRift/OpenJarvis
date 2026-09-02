import { afterEach, describe, expect, it, vi } from 'vitest';
import { clipsForVoice } from './greeting';

const manifest = {
  default_voice_id: 'jarvis',
  voices: {
    jarvis: ['greetings/jarvis/hello-sir-sonic36.mp3'],
    frieren: ['greetings/frieren/hello-sir-sonic36.mp3'],
  },
};

describe('wake greeting voice selection', () => {
  it('selects the active voice recordings', () => {
    expect(clipsForVoice(manifest, 'frieren')).toEqual(manifest.voices.frieren);
  });

  it('falls back to Jarvis when a removed voice was stored', () => {
    expect(clipsForVoice(manifest, 'removed')).toEqual(manifest.voices.jarvis);
  });
});

// A clip that starts and then stalls fires neither `ended` nor `error`. The
// wake word awaits this promise while holding its busy flag, so one stalled
// greeting disarmed the wake word for the whole session — the user had to
// reload the page to get it back.
describe('playGreeting never leaves the caller waiting', () => {
  const manifestJson = {
    default_voice_id: 'jarvis',
    voices: { jarvis: ['greetings/jarvis/hello.mp3'] },
  };

  function stubAudio(behaviour: 'stall' | 'ended') {
    class FakeAudio {
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(public src: string) {}
      play() {
        if (behaviour === 'ended') queueMicrotask(() => this.onended?.());
        return Promise.resolve();
      }
    }
    vi.stubGlobal('Audio', FakeAudio as unknown as typeof Audio);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => manifestJson }),
    );
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    vi.resetModules();
  });

  it('resolves when the clip plays through', async () => {
    stubAudio('ended');
    const { playGreeting } = await import('./greeting');

    await expect(playGreeting({ voiceId: 'jarvis' })).resolves.toBeUndefined();
  });

  it('resolves on a timeout when playback stalls, and says why', async () => {
    stubAudio('stall');
    vi.useFakeTimers();
    const { playGreeting } = await import('./greeting');
    const onFailure = vi.fn();

    const pending = playGreeting({ voiceId: 'jarvis', onFailure });
    await vi.advanceTimersByTimeAsync(10_000);

    await expect(pending).resolves.toBeUndefined();
    expect(onFailure).toHaveBeenCalledWith(expect.stringContaining('stalled'));
  });
});
