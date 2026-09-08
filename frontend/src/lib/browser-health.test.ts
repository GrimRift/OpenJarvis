import { describe, it, expect } from 'vitest';
import { evaluateMicrophone } from './browser-health';

/**
 * The wake word once fired while the microphone was muted, and no server-side
 * check could ever have seen it. The decision logic is a pure function so it
 * is testable at all: vitest here has no jsdom, so anything touching a real
 * navigator would be untestable.
 */

const mic = (label = '') => ({ kind: 'audioinput', label, deviceId: 'a' });

describe('evaluateMicrophone', () => {
  it('reports denied permission as a failure', () => {
    const checks = evaluateMicrophone([mic()], 'denied');
    const permission = checks.find((c) => c.name === 'Microphone permission');
    expect(permission?.status).toBe('fail');
  });

  it('reports granted permission as ok', () => {
    const checks = evaluateMicrophone([mic('Headset')], 'granted');
    expect(checks.find((c) => c.name === 'Microphone permission')?.status).toBe('ok');
  });

  it('treats an unknown permission as a warning, not a failure', () => {
    // Firefox has no microphone descriptor; that is not a denial.
    const checks = evaluateMicrophone([mic()], null);
    expect(checks.find((c) => c.name === 'Microphone permission')?.status).toBe('warn');
  });

  it('fails when there is no input device at all', () => {
    const checks = evaluateMicrophone([{ kind: 'audiooutput' }], 'granted');
    const devices = checks.find((c) => c.name === 'Microphone devices');
    expect(devices?.status).toBe('fail');
    expect(devices?.message).toContain('No microphone');
  });

  it('names the device when a label is available', () => {
    const checks = evaluateMicrophone([mic('Blue Yeti')], 'granted');
    expect(checks.find((c) => c.name === 'Microphone devices')?.message).toContain(
      'Blue Yeti',
    );
  });

  it('explains empty labels rather than treating them as broken', () => {
    // Labels stay empty until permission is granted; that is the browser
    // hiding names, not a fault.
    const checks = evaluateMicrophone([mic('')], 'prompt');
    const devices = checks.find((c) => c.name === 'Microphone devices');
    expect(devices?.status).toBe('ok');
    expect(devices?.details).toContain('hidden until');
  });

  it('counts multiple microphones', () => {
    const checks = evaluateMicrophone([mic('A'), mic('B')], 'granted');
    expect(checks.find((c) => c.name === 'Microphone devices')?.message).toContain('2');
  });
});
