import { useEffect, useState } from 'react';
import { useAppStore } from '../../lib/store';
import { microphoneOptions, type MicOption } from '../../lib/mic-device';

/**
 * Which microphone Sage listens with: the wake word, what you say, and
 * dictation. Labels need microphone permission, which the app already has
 * by the time anyone opens Settings; the list follows plugging and
 * unplugging (and virtual mics like NVIDIA Broadcast coming and going).
 */
export function MicrophonePicker({ onSaved }: { onSaved: () => void }) {
  const micDeviceId = useAppStore((s) => s.settings.micDeviceId);
  const micDeviceLabel = useAppStore((s) => s.settings.micDeviceLabel);
  const updateSettings = useAppStore((s) => s.updateSettings);
  const [options, setOptions] = useState<MicOption[]>([]);
  const [defaultLabel, setDefaultLabel] = useState('');

  useEffect(() => {
    const media = navigator.mediaDevices;
    if (!media?.enumerateDevices) return;
    let gone = false;
    const refresh = () => {
      media
        .enumerateDevices()
        .then((devices) => {
          if (gone) return;
          setOptions(microphoneOptions(devices));
          const def = devices.find((d) => d.kind === 'audioinput' && d.deviceId === 'default');
          setDefaultLabel(def?.label.replace(/^Default - /, '') ?? '');
        })
        .catch(() => {});
    };
    refresh();
    media.addEventListener?.('devicechange', refresh);
    return () => {
      gone = true;
      media.removeEventListener?.('devicechange', refresh);
    };
  }, []);

  const missing = !!micDeviceId && !options.some((o) => o.id === micDeviceId);

  return (
    <select
      aria-label="Microphone"
      value={micDeviceId}
      onChange={(event) => {
        const id = event.target.value;
        const label = options.find((o) => o.id === id)?.label ?? '';
        updateSettings({ micDeviceId: id, micDeviceLabel: id ? label : '' });
        onSaved();
      }}
      className="px-2 py-1 rounded-lg text-sm max-w-64"
      style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
    >
      <option value="">{defaultLabel ? `Windows default (${defaultLabel})` : 'Windows default'}</option>
      {options.map((o) => (
        <option key={o.id} value={o.id}>
          {o.label}
        </option>
      ))}
      {missing && (
        <option value={micDeviceId}>{`${micDeviceLabel || 'Chosen microphone'} (not connected)`}</option>
      )}
    </select>
  );
}
