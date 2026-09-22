import { useEffect, useRef, useState } from 'react';
import { Upload, Trash2, Play, RefreshCw } from 'lucide-react';
import {
  deleteSpeechVoice,
  fetchSpeechVoices,
  previewVoice,
  saveVoiceChoice,
  uploadSpeechVoice,
  type LocalVoiceInfo,
  type ProviderStatus,
  type SpeechHealth,
} from '../../lib/api';
import { useAppStore, type SttProvider, type TtsProvider } from '../../lib/store';
import {
  CHATTERBOX_VOICE_PREFIX,
  DEFAULT_VOICE_PROFILE,
  getVoiceProfile,
  profilesFor,
} from '../../lib/voice-profiles';
import { getBase } from '../../lib/api';
import { setBoostedVolume } from '../../lib/audio-out';

const selectStyle = {
  background: 'var(--color-bg-secondary)',
  color: 'var(--color-text)',
  border: '1px solid var(--color-border)',
} as const;

function Dot({ ok }: { ok: boolean | null }) {
  const color =
    ok === null ? 'var(--color-text-tertiary)' : ok ? 'var(--color-success, #22c55e)' : 'var(--color-error, #ef4444)';
  return <span className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle" style={{ background: color }} />;
}

function describe(status: ProviderStatus | undefined, checking: boolean): { ok: boolean | null; text: string } {
  if (checking || !status) return { ok: null, text: 'Checking…' };
  if (!status.available) return { ok: false, text: status.reason || 'Unavailable' };
  const bits: string[] = [];
  if (status.model_loaded === false && status.sidecar_running === false) bits.push('starts on first use');
  else if (status.model_loaded === false && status.sidecar_running) bits.push('starting…');
  else if (status.model_loaded) bits.push(`loaded on ${status.device ?? '?'}`);
  else if (status.loaded) bits.push(`loaded on ${status.device ?? '?'}`);
  else if (status.requested_device) bits.push(`ready (${status.requested_device})`);
  else bits.push('ready');
  if (status.reason && status.available) bits.push(status.reason);
  return { ok: true, text: bits.join(' — ') };
}

interface Props {
  health: SpeechHealth | null;
  checking: boolean;
  onRefresh: () => void;
  onSaved: () => void;
  /** The parent's row/switch primitives, so the block matches the page. */
  Row: React.ComponentType<{ label: string; description?: string; children: React.ReactNode }>;
  Switch: React.ComponentType<{ on: boolean; onClick: () => void; disabled?: boolean }>;
}

/**
 * Speech-to-text and voice engine selection, plus the local voice's
 * reference recording. The two providers are chosen independently; "Use
 * local voice models" is a convenience that sets both and touches nothing
 * else (the language model stays whatever the picker says).
 */
export function VoiceProviders({ health, checking, onRefresh, onSaved, Row, Switch }: Props) {
  const settings = useAppStore((s) => s.settings);
  const updateSettings = useAppStore((s) => s.updateSettings);
  const [localVoices, setLocalVoices] = useState<LocalVoiceInfo[]>([]);
  const [busy, setBusy] = useState<string>('');
  const [error, setError] = useState<string>('');
  const fileRef = useRef<HTMLInputElement>(null);

  const flux = describe(health?.stt?.flux, checking);
  const parakeet = describe(health?.stt?.parakeet, checking);
  const cartesia = describe(health?.tts?.cartesia, checking);
  const chatterbox = describe(health?.tts?.chatterbox, checking);

  const refreshVoices = () => {
    fetchSpeechVoices()
      .then((v) => setLocalVoices(v.chatterbox.voices))
      .catch(() => setLocalVoices([]));
  };
  useEffect(refreshVoices, []);

  // While the local voice is still loading (typically the first minute
  // after a boot), poll so the row fills in without a manual refresh.
  const chatterboxLoading =
    settings.ttsProvider === 'chatterbox' &&
    health?.tts?.chatterbox?.available === true &&
    !health?.tts?.chatterbox?.model_loaded;
  useEffect(() => {
    if (!chatterboxLoading) return;
    const timer = setInterval(() => {
      onRefresh();
      refreshVoices();
    }, 5000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatterboxLoading]);

  const allLocal = settings.sttProvider === 'parakeet' && settings.ttsProvider === 'chatterbox';
  const voiceNames = localVoices.map((v) => v.name);
  const voiceProfiles = profilesFor(settings.ttsProvider, voiceNames);
  const currentVoice = getVoiceProfile(settings.ttsVoiceId, settings.ttsProvider);
  const currentLocal = localVoices.find((v) => CHATTERBOX_VOICE_PREFIX + v.name === currentVoice.id);

  // Server-side speech (moments, reminders) cannot read localStorage, so
  // the choice is mirrored to the server whenever it changes here.
  useEffect(() => {
    saveVoiceChoice(settings.ttsProvider, currentVoice.id).catch(() => {});
  }, [settings.ttsProvider, currentVoice.id]);

  const setStt = (value: SttProvider) => {
    updateSettings({ sttProvider: value });
    onSaved();
  };
  const setTts = (value: TtsProvider) => {
    // Keep a voice that belongs to the new engine selected, so the next
    // reply does not resolve to a default the user never chose.
    const voice = getVoiceProfile(settings.ttsVoiceId, value);
    updateSettings({ ttsProvider: value, ttsVoiceId: voice.id });
    onSaved();
  };

  const upload = async (file: File) => {
    const name = window.prompt('Name for this voice (letters, digits, dashes):', currentLocal?.name || 'jarvis');
    if (!name) return;
    setBusy('upload');
    setError('');
    try {
      const info = await uploadSpeechVoice(name.trim().toLowerCase(), file);
      refreshVoices();
      updateSettings({ ttsProvider: 'chatterbox', ttsVoiceId: CHATTERBOX_VOICE_PREFIX + info.name });
      onSaved();
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const remove = async () => {
    if (!currentLocal) return;
    if (!window.confirm(`Remove the local voice "${currentLocal.name}" and its recording?`)) return;
    setBusy('remove');
    setError('');
    try {
      await deleteSpeechVoice(currentLocal.name);
      refreshVoices();
      updateSettings({ ttsVoiceId: DEFAULT_VOICE_PROFILE.id });
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const testVoice = async () => {
    setBusy('test');
    setError('');
    try {
      const meta = await previewVoice(
        'Good evening, Sir. All systems are running within normal parameters.',
        {
          voice_id: currentVoice.id,
          speed: currentVoice.speed,
          volume: currentVoice.volume,
          backend: currentVoice.provider,
        },
      );
      const audio = new Audio(`${getBase()}${meta.url}`);
      setBoostedVolume(audio, 'chat');
      await audio.play();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  return (
    <>
      <Row
        label="Use local voice models"
        description="Run speech-to-text (NVIDIA Parakeet) and the voice (Chatterbox Nano) on this machine. The language model is unchanged."
      >
        <Switch
          on={allLocal}
          onClick={() => {
            const next = !allLocal;
            const ttsProvider: TtsProvider = next ? 'chatterbox' : 'cartesia';
            updateSettings({
              sttProvider: next ? 'parakeet' : 'flux',
              ttsProvider,
              ttsVoiceId: getVoiceProfile(settings.ttsVoiceId, ttsProvider).id,
            });
            onSaved();
          }}
        />
      </Row>

      <Row
        label="Speech-to-text engine"
        description={
          settings.sttProvider === 'flux'
            ? `Deepgram Flux — cloud. ${flux.text}`
            : settings.sttProvider === 'parakeet'
              ? `NVIDIA Parakeet — local. ${parakeet.text}`
              : 'Local Whisper — push-to-talk; the fallback for both streaming engines.'
        }
      >
        <select
          aria-label="Speech-to-text engine"
          value={settings.sttProvider}
          onChange={(e) => setStt(e.target.value as SttProvider)}
          className="px-2 py-1 rounded-lg text-sm"
          style={selectStyle}
        >
          <option value="flux" disabled={flux.ok === false}>Deepgram Flux — Cloud</option>
          <option value="parakeet" disabled={parakeet.ok === false}>NVIDIA Parakeet — Local</option>
          <option value="whisper">Local Whisper — Push to talk</option>
        </select>
      </Row>
      <div className="text-xs px-1 -mt-1 mb-2" style={{ color: 'var(--color-text-tertiary)' }}>
        <Dot ok={flux.ok} />Flux: {flux.text}
        <span className="mx-2">·</span>
        <Dot ok={parakeet.ok} />Parakeet: {parakeet.text}
      </div>

      <Row
        label="Voice engine"
        description={
          settings.ttsProvider === 'cartesia'
            ? `Cartesia — cloud. ${cartesia.text}`
            : `Chatterbox Nano — local, cloned from your reference recording. ${chatterbox.text}`
        }
      >
        <select
          aria-label="Voice engine"
          value={settings.ttsProvider}
          onChange={(e) => setTts(e.target.value as TtsProvider)}
          className="px-2 py-1 rounded-lg text-sm"
          style={selectStyle}
        >
          <option value="cartesia" disabled={cartesia.ok === false}>Cartesia — Cloud</option>
          <option value="chatterbox" disabled={chatterbox.ok === false}>Chatterbox Nano — Local</option>
        </select>
      </Row>
      <div className="text-xs px-1 -mt-1 mb-2" style={{ color: 'var(--color-text-tertiary)' }}>
        <Dot ok={cartesia.ok} />Cartesia: {cartesia.text}
        <span className="mx-2">·</span>
        <Dot ok={chatterbox.ok} />Chatterbox: {chatterbox.text}
        <button onClick={onRefresh} className="ml-2 align-middle" title="Refresh status" aria-label="Refresh voice status">
          <RefreshCw size={11} />
        </button>
      </div>

      <Row label="Sage voice" description="Used for voice replies, morning-digest playback, and wake-word greetings">
        <select
          aria-label="Sage voice"
          value={currentVoice.id}
          onChange={(event) => {
            updateSettings({ ttsVoiceId: event.target.value });
            onSaved();
          }}
          className="px-3 py-2 rounded-lg text-sm cursor-pointer"
          style={{
            background: 'var(--color-bg-tertiary)',
            color: 'var(--color-text-primary)',
            border: '1px solid var(--color-border)',
          }}
        >
          {voiceProfiles.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.name}
            </option>
          ))}
        </select>
      </Row>

      {settings.ttsProvider === 'chatterbox' && (
        <Row
          label="Reference recording"
          description={
            currentLocal?.has_reference
              ? `${currentLocal.name}: ${currentLocal.reference_seconds ?? '?'} s of reference audio${currentLocal.has_conditioning ? ', conditioning cached' : ''}. Ten seconds or more of clean speech works best.`
              : 'No recording yet for this voice. Upload 10 s or more of clean speech (WAV, MP3 or FLAC).'
          }
        >
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".wav,.mp3,.flac,.ogg,.m4a,audio/*"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload(f);
                e.target.value = '';
              }}
            />
            <button
              onClick={() => fileRef.current?.click()}
              disabled={busy !== ''}
              className="px-2 py-1 rounded-lg text-xs flex items-center gap-1"
              style={selectStyle}
              title="Upload a reference recording"
            >
              <Upload size={12} /> {busy === 'upload' ? 'Uploading…' : 'Upload'}
            </button>
            <button
              onClick={testVoice}
              disabled={busy !== '' || !currentLocal?.has_reference}
              className="px-2 py-1 rounded-lg text-xs flex items-center gap-1"
              style={selectStyle}
              title="Speak a test sentence in this voice"
            >
              <Play size={12} /> {busy === 'test' ? 'Speaking…' : 'Test'}
            </button>
            <button
              onClick={remove}
              disabled={busy !== '' || !currentLocal}
              className="px-2 py-1 rounded-lg text-xs flex items-center gap-1"
              style={{ ...selectStyle, color: 'var(--color-error, #ef4444)' }}
              title="Remove this voice"
            >
              <Trash2 size={12} />
            </button>
          </div>
        </Row>
      )}
      {error && (
        <div className="text-xs px-1 mb-2" style={{ color: 'var(--color-error, #ef4444)' }}>
          {error}
        </div>
      )}
    </>
  );
}
