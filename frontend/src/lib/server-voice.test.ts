import { beforeEach, describe, expect, it } from 'vitest';
import {
  AUDIO_LEAD_MS,
  _resetServerVoice,
  applyServerVoice,
  serverVoiceLevel,
  serverVoiceTimeoutMs,
  syntheticSpeech,
  takeSseEvents,
} from './server-voice';
import { resolveOrbState } from './orb-state';

beforeEach(() => _resetServerVoice());

/** Run the level forward frame by frame and return where it settles. */
function settle(at: number, frames = 30): number {
  let v = 0;
  for (let i = 0; i < frames; i++) v = serverVoiceLevel(at + i * (1000 / 60));
  return v;
}

describe('the stream', () => {
  it('reads events, however the network splits them', () => {
    const first = takeSseEvents('retry: 2000\n\ndata: {"speaking":tr');
    expect(first.events).toEqual([]);
    const second = takeSseEvents(first.rest + 'ue,"id":3}\n\n: keepalive\n\n');
    expect(second.events).toEqual([{ speaking: true, id: 3 }]);
    expect(second.rest).toBe('');
  });

  it('skips a malformed event without losing the next', () => {
    const { events } = takeSseEvents('data: {oops\n\ndata: {"speaking":false}\n\n');
    expect(events).toEqual([{ speaking: false }]);
  });

  it('accepts CRLF line endings', () => {
    expect(takeSseEvents('data: {"speaking":false}\r\n\r\n').events).toEqual([{ speaking: false }]);
  });
});

describe('the orb speaks with the server', () => {
  it('switches to speaking for a server voice', () => {
    const inputs = { audioPlaying: false, streamingHere: false, voiceState: 'idle', presenceState: 'present' };
    expect(resolveOrbState(inputs)).toBe('idle');
    expect(resolveOrbState({ ...inputs, serverSpeaking: true })).toBe('speaking');
    // Even with nobody at the desk: it is talking.
    expect(resolveOrbState({ ...inputs, presenceState: 'away', serverSpeaking: true })).toBe('speaking');
  });

  it('follows the envelope, read as the sound is heard', () => {
    // Silent for 200 ms, then loud.
    const envelope = [...new Array(10).fill(0), ...new Array(40).fill(0.9)];
    applyServerVoice({ speaking: true, id: 1, elapsed_ms: 0, step_ms: 20, envelope }, 1000);
    // Before the sound is heard, and through its silent start: at rest.
    expect(settle(1000 + AUDIO_LEAD_MS + 20, 5)).toBeLessThan(0.05);
    // Once the loud part is heard: up.
    expect(settle(1000 + AUDIO_LEAD_MS + 300, 20)).toBeGreaterThan(0.8);
  });

  it('lines up a voice it tuned into part-way', () => {
    const envelope = [...new Array(50).fill(0), ...new Array(50).fill(0.9)];
    // Already 1s in when the page hears of it: the loud half is playing.
    applyServerVoice({ speaking: true, id: 2, elapsed_ms: 1000, step_ms: 20, envelope }, 5000);
    expect(settle(5000 + AUDIO_LEAD_MS, 20)).toBeGreaterThan(0.8);
  });

  it('still talks when there is no envelope to follow', () => {
    applyServerVoice({ speaking: true, id: 3, elapsed_ms: 0, envelope: null }, 0);
    const levels: number[] = [];
    for (let i = 0; i < 120; i++) levels.push(serverVoiceLevel(AUDIO_LEAD_MS + i * (1000 / 60)));
    expect(Math.max(...levels)).toBeGreaterThan(0.3);
    expect(Math.min(...levels.slice(20))).toBeLessThan(Math.max(...levels) * 0.6);
    expect(syntheticSpeech(0)).toBeGreaterThanOrEqual(0);
  });

  it('settles when the voice stops', () => {
    applyServerVoice({ speaking: true, id: 4, elapsed_ms: 0, step_ms: 20, envelope: new Array(100).fill(0.9) }, 0);
    expect(settle(AUDIO_LEAD_MS + 100)).toBeGreaterThan(0.8);
    applyServerVoice({ speaking: false }, 2000);
    expect(settle(2000, 60)).toBeLessThan(0.02);
  });

  it('gives up on a stop that never arrives', () => {
    // A measured voice: its own length and ten seconds.
    expect(serverVoiceTimeoutMs({ speaking: true, step_ms: 20, envelope: new Array(250).fill(0.5) })).toBe(15_000);
    // Unmeasured: two minutes, longer than any reminder.
    expect(serverVoiceTimeoutMs({ speaking: true, envelope: null })).toBe(120_000);
  });
});
