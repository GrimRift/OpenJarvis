import { useState } from 'react';
import { digitsOf, markDevicePaired, redeemPairingCode } from '../../lib/pairing';
import { useAppStore } from '../../lib/store';

/**
 * A phone or iPad without Sage's key (M29): type the six-digit code shown in
 * Settings on the PC. Shown instead of the app until it works.
 */
export function PairScreen({ onPaired, again }: { onPaired: () => void; again: boolean }) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  const submit = async (typed: string) => {
    if (digitsOf(typed).length !== 6 || busy) return;
    setBusy(true);
    setMessage('');
    const result = await redeemPairingCode(typed);
    setBusy(false);
    if (!result.ok) {
      setMessage(result.message);
      setCode('');
      return;
    }
    // The PC's settings first (voice, wake word, speech engine), then the key.
    useAppStore.getState().updateSettings({ ...result.settings, apiKey: result.apiKey });
    markDevicePaired();
    onPaired();
  };

  return (
    <div className="touch-shell relative flex h-full w-full flex-col items-center justify-center gap-5 px-8 text-center">
      <div className="hud-backdrop" aria-hidden="true" />
      <span className="sage-wordmark relative select-none" style={{ fontSize: 20 }}>SAGE</span>
      <div className="relative text-[19px] font-semibold" style={{ color: 'var(--color-text)' }}>
        {again ? 'Pair this device again' : 'Pair this device'}
      </div>
      {again && (
        <p className="relative text-[13px] max-w-[320px]" style={{ color: 'var(--color-text-tertiary)' }}>
          Once more, to bring over the PC's voice and settings.
        </p>
      )}
      <p className="relative text-[14px] leading-relaxed max-w-[320px]" style={{ color: 'var(--color-text-secondary)' }}>
        On the PC, open Sage's <b style={{ color: 'var(--color-text)' }}>Settings</b> and press{' '}
        <b style={{ color: 'var(--color-text)' }}>Pair a phone or iPad</b>. Type the six digits it shows.
      </p>
      <input
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        autoFocus
        value={code}
        disabled={busy}
        onChange={(e) => {
          const next = digitsOf(e.target.value);
          setCode(next);
          if (next.length === 6) void submit(next);
        }}
        placeholder="000000"
        aria-label="Pairing code"
        className="relative w-[220px] rounded-2xl px-4 py-3 text-center outline-none"
        style={{
          fontSize: 32,
          letterSpacing: '0.35em',
          background: 'var(--color-bg-secondary)',
          border: '1px solid var(--color-accent)',
          color: 'var(--color-text)',
          boxShadow: '0 0 24px var(--color-accent-glow)',
        }}
      />
      <div className="relative h-5 text-[13px]" style={{ color: message ? 'var(--color-error)' : 'var(--color-text-tertiary)' }}>
        {busy ? 'Checking…' : message}
      </div>
    </div>
  );
}
