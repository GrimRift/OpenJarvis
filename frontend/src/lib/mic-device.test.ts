import { afterEach, describe, expect, it, vi } from 'vitest';
import { microphoneOptions, openMicrophone, resolveMic } from './mic-device';

function dev(deviceId: string, label: string, kind: MediaDeviceKind = 'audioinput'): MediaDeviceInfo {
  return { deviceId, label, kind, groupId: '', toJSON: () => ({}) } as MediaDeviceInfo;
}

const DEVICES = [
  dev('default', 'Default - Microphone (NVIDIA Broadcast)'),
  dev('communications', 'Communications - Microphone (PD100X Podcast Microphone)'),
  dev('pd', 'Microphone (PD100X Podcast Microphone)'),
  dev('nv', 'Microphone (NVIDIA Broadcast)'),
  dev('spk', 'Speakers', 'audiooutput'),
];

describe('microphoneOptions', () => {
  it('lists real inputs only', () => {
    expect(microphoneOptions(DEVICES).map((o) => o.id)).toEqual(['pd', 'nv']);
  });

  it('names an unlabelled input', () => {
    expect(microphoneOptions([dev('x', '')])[0].label).toBe('Microphone 1');
  });
});

describe('resolveMic', () => {
  it('keeps the default when nothing is chosen', () => {
    expect(resolveMic({ id: '', label: '' }, DEVICES)).toBe('');
  });

  it('keeps an id that is still there', () => {
    expect(resolveMic({ id: 'nv', label: 'whatever' }, DEVICES)).toBe('nv');
  });

  it('finds the same mic under a new id by its name', () => {
    expect(resolveMic({ id: 'old', label: 'Microphone (PD100X Podcast Microphone)' }, DEVICES)).toBe('pd');
  });

  it('falls back to the default when the mic is gone', () => {
    expect(resolveMic({ id: 'old', label: 'Microphone (Gone)' }, DEVICES)).toBe('');
  });
});

describe('openMicrophone', () => {
  const stream = {} as MediaStream;

  function mockMedia(getUserMedia: (c: MediaStreamConstraints) => Promise<MediaStream>) {
    vi.stubGlobal('navigator', {
      mediaDevices: { getUserMedia: vi.fn(getUserMedia), enumerateDevices: vi.fn(async () => DEVICES) },
    });
    return navigator.mediaDevices.getUserMedia as unknown as ReturnType<typeof vi.fn>;
  }

  afterEach(() => vi.unstubAllGlobals());

  it('opens the chosen device exactly, keeping the other constraints', async () => {
    const gum = mockMedia(async () => stream);
    await openMicrophone({ echoCancellation: true }, { id: 'nv', label: '' });
    expect(gum).toHaveBeenCalledWith({ audio: { echoCancellation: true, deviceId: { exact: 'nv' } } });
  });

  it('opens the default with no choice', async () => {
    const gum = mockMedia(async () => stream);
    await openMicrophone({ echoCancellation: true }, { id: '', label: '' });
    expect(gum).toHaveBeenCalledWith({ audio: { echoCancellation: true } });
  });

  it('falls back to the default when the chosen device will not open', async () => {
    const gum = mockMedia(async (c) => {
      if (typeof c.audio === 'object' && 'deviceId' in c.audio) {
        throw Object.assign(new Error('gone'), { name: 'OverconstrainedError' });
      }
      return stream;
    });
    await expect(openMicrophone({}, { id: 'nv', label: '' })).resolves.toBe(stream);
    expect(gum).toHaveBeenLastCalledWith({ audio: {} });
  });

  it('does not hide a permission refusal', async () => {
    mockMedia(async () => {
      throw Object.assign(new Error('no'), { name: 'NotAllowedError' });
    });
    await expect(openMicrophone({}, { id: 'nv', label: '' })).rejects.toThrow('no');
  });
});
