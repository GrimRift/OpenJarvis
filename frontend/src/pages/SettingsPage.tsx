import { useState, useEffect, useCallback } from 'react';
import {
  Palette,
  Globe,
  Cpu,
  Database,
  Info,
  Check,
  Sun,
  Moon,
  Monitor,
  Download,
  Upload,
  Trash2,
  Mic,
  Key,
  Search,
  Brain,
  RefreshCw,
} from 'lucide-react';
import { useAppStore, LISTEN_SECONDS_MAX, LISTEN_SECONDS_MIN, type OrbDesign, type ThemeMode, type WakeWordVerify } from '../lib/store';
import { VoiceProviders } from '../components/Settings/VoiceProviders';
import type { BargeMode } from '../lib/barge-in';
import { fetchVolumes, updateVolumes, type Volumes } from '../lib/volume';
import { fetchKeyterms, parseTerms, saveKeyterms, type Keyterms } from '../lib/keyterms';

const VOLUME_ROWS: Array<[keyof Volumes, string, string]> = [
  ['master', 'Master', 'everything Sage says or plays'],
  ['chat', 'Chat replies', 'answers read aloud in the chat'],
  ['ack', 'Wake-word acknowledgement', '"Yes, Sir?" and "one moment"'],
  ['moments', 'Sage speaking first', 'greeting, welcome back, initiative, tell-me-when'],
  ['reminders', 'Reminders', 'timed reminders and desktop alerts'],
  ['chime', 'Chime', 'the tone before Sage speaks first'],
];
import { modelForToggle } from '../lib/model-preference';
import {
  checkHealth,
  fetchSpeechHealth,
  getMemoryStats,
  getInferenceSource,
  setInferenceSource,
  getCloudKeyStatus,
  saveCloudKey,
  fetchToolCredentialStatus,
  saveToolCredentials,
  deleteToolCredential,
  isTauri,
  fetchPresenceSettings,
  updatePresenceSettings,
  fetchMoments,
  setMomentsSnoozed,
  cancelMomentWatch,
  type MomentsSnapshot,
  type InferenceSource,
  type PresenceSettings,
  type SpeechHealth,
} from '../lib/api';
import { isAutoUpdateDisabled, setAutoUpdateDisabled } from '../components/Desktop/UpdateChecker';

const CLOUD_KEY_STATUS_CHANGED = 'openjarvis-cloud-key-status-changed';

function OllamaModelList() {
  const [models, setModels] = useState<Array<{ name: string; size: number }>>([]);
  useEffect(() => {
    fetch('http://localhost:11434/api/tags')
      .then(r => r.json())
      .then(data => setModels((data.models || []).map((m: any) => ({ name: m.name, size: m.size }))))
      .catch(() => setModels([]));
  }, []);
  if (models.length === 0) return <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>No models loaded</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {models.map(m => (
        <span key={m.name} className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px]"
          style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-success)', display: 'inline-block' }} />
          {m.name} ({(m.size / 1e9).toFixed(1)} GB)
        </span>
      ))}
    </div>
  );
}

function ApiKeyInput({
  keyName,
  placeholder,
  toolName,
}: {
  keyName: string;
  placeholder: string;
  toolName?: string;
}) {
  const [value, setValue] = useState('');
  const [saved, setSaved] = useState(false);
  const [hasKey, setHasKey] = useState(false);
  const [error, setError] = useState('');
  const desktopKeyStorage = isTauri();
  const serverToolStorage = !desktopKeyStorage && !!toolName;
  const canManage = desktopKeyStorage || serverToolStorage;

  const refresh = useCallback(async () => {
    if (!canManage) {
      setHasKey(false);
      return;
    }
    try {
      const status = desktopKeyStorage
        ? await getCloudKeyStatus()
        : await fetchToolCredentialStatus(toolName!);
      setHasKey(!!status[keyName]);
    } catch {
      setHasKey(false);
    }
  }, [canManage, desktopKeyStorage, keyName, toolName]);

  useEffect(() => {
    void refresh();
    window.addEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
    return () => window.removeEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
  }, [refresh]);

  const save = async (v: string) => {
    const next = v.trim();
    if (!next) return;
    setError('');
    try {
      if (desktopKeyStorage) {
        await saveCloudKey(keyName, next);
      } else if (toolName) {
        await saveToolCredentials(toolName, { [keyName]: next });
      } else {
        return;
      }
      setValue('');
      setHasKey(true);
      setSaved(true);
      window.dispatchEvent(new Event(CLOUD_KEY_STATUS_CHANGED));
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setError(e?.message || 'Failed to save API key');
    }
  };

  const remove = async () => {
    setError('');
    try {
      if (desktopKeyStorage) {
        await saveCloudKey(keyName, '');
      } else if (toolName) {
        await deleteToolCredential(toolName, keyName);
      } else {
        return;
      }
      setValue('');
      setHasKey(false);
      setSaved(true);
      window.dispatchEvent(new Event(CLOUD_KEY_STATUS_CHANGED));
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setError(e?.message || 'Failed to remove API key');
    }
  };

  return (
    <div className="flex items-center gap-2">
      <input
        type="password"
        value={value}
        onChange={e => setValue(e.target.value)}
        onBlur={() => { if (value.trim()) void save(value); }}
        placeholder={hasKey ? (desktopKeyStorage ? 'Saved in secure storage' : 'Saved by local server') : placeholder}
        disabled={!canManage}
        className="w-48 px-2 py-1 rounded text-xs"
        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />
      {hasKey && (
        <button
          onClick={() => void remove()}
          className="px-2 py-1 rounded text-[10px] cursor-pointer"
          style={{ color: 'var(--color-error)', border: '1px solid var(--color-error)' }}
        >
          Remove
        </button>
      )}
      {saved && <span className="text-[10px]" style={{ color: 'var(--color-success)' }}>Saved</span>}
      {error && <span className="text-[10px]" style={{ color: 'var(--color-error)' }}>{error}</span>}
    </div>
  );
}

function CloudProviderStatus({ label, keyName }: { label: string; keyName: string }) {
  const [hasKey, setHasKey] = useState(false);
  const desktopKeyStorage = isTauri();

  const refresh = useCallback(async () => {
    if (!desktopKeyStorage) {
      setHasKey(false);
      return;
    }
    try {
      const status = await getCloudKeyStatus();
      setHasKey(!!status[keyName]);
    } catch {
      setHasKey(false);
    }
  }, [desktopKeyStorage, keyName]);

  useEffect(() => {
    void refresh();
    window.addEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
    return () => window.removeEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
  }, [refresh]);

  return (
    <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%', display: 'inline-block',
        background: hasKey ? 'var(--color-success)' : 'var(--color-text-tertiary)',
      }} />
      {label}
    </span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-xl p-5"
      style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
    >
      <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--color-text)' }}>
        {title}
      </h3>
      {children}
    </div>
  );
}

function SettingRow({ label, description, children }: { label: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-3" style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>
      <div>
        <div className="text-sm" style={{ color: 'var(--color-text)' }}>{label}</div>
        {description && (
          <div className="text-xs mt-0.5" style={{ color: 'var(--color-text-tertiary)' }}>{description}</div>
        )}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Switch({ on, onClick, disabled }: { on: boolean; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="relative w-11 h-6 rounded-full transition-colors cursor-pointer disabled:opacity-50"
      style={{ background: on ? 'var(--color-accent)' : 'var(--color-bg-tertiary)' }}
    >
      <span
        className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
        style={{ transform: on ? 'translateX(20px)' : 'translateX(0)', boxShadow: '0 1px 3px rgba(0,0,0,0.2)' }}
      />
    </button>
  );
}

const themeOptions: { value: ThemeMode; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
];

export function SettingsPage() {
  const settings = useAppStore((s) => s.settings);
  const models = useAppStore((s) => s.models);
  const cloudModelAvailable = useAppStore((s) => s.cloudModelAvailable);
  const setSelectedModel = useAppStore((s) => s.setSelectedModel);
  const updateSettings = useAppStore((s) => s.updateSettings);
  const conversations = useAppStore((s) => s.conversations);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [speechBackendAvailable, setSpeechBackendAvailable] = useState<boolean | null>(null);
  const [wakeWordAvailable, setWakeWordAvailable] = useState<boolean | null>(null);
  const [fluxAvailable, setFluxAvailable] = useState<boolean | null>(null);
  const [fluxReason, setFluxReason] = useState<string>('');
  const [speechHealth, setSpeechHealth] = useState<SpeechHealth | null>(null);
  const [speechChecking, setSpeechChecking] = useState(true);
  const [saved, setSaved] = useState(false);
  const [volumes, setVolumes] = useState<Volumes | null>(null);
  const [volumeError, setVolumeError] = useState<string | null>(null);
  useEffect(() => {
    fetchVolumes()
      .then(setVolumes)
      .catch((err) => setVolumeError(err instanceof Error ? err.message : String(err)));
  }, []);
  const patchVolume = async (patch: Partial<Volumes>) => {
    // Optimistic: the slider must follow the hand, not the round trip.
    setVolumes((v) => (v ? { ...v, ...patch } : v));
    try {
      setVolumes(await updateVolumes(patch));
      setVolumeError(null);
      showSaved();
    } catch (err) {
      setVolumeError(err instanceof Error ? err.message : String(err));
    }
  };
  const [presence, setPresence] = useState<PresenceSettings | null>(null);
  const [presenceError, setPresenceError] = useState<string | null>(null);
  useEffect(() => {
    fetchPresenceSettings()
      .then(setPresence)
      .catch((err) => setPresenceError(err instanceof Error ? err.message : String(err)));
  }, []);
  const patchPresence = async (patch: Partial<PresenceSettings>) => {
    if (!presence) return;
    try {
      setPresence(await updatePresenceSettings(patch));
      setPresenceError(null);
      showSaved();
    } catch (err) {
      setPresenceError(err instanceof Error ? err.message : String(err));
    }
  };
  const togglePresence = () => presence && patchPresence({ enabled: !presence.enabled });
  const flip = (key: keyof PresenceSettings) => () =>
    presence && patchPresence({ [key]: !presence[key] } as Partial<PresenceSettings>);

  // Moments: the server's record of what Sage said first, pending watches,
  // and today's quiet. Server-side state, so it is read rather than stored.
  const [moments, setMoments] = useState<MomentsSnapshot | null>(null);
  const refreshMoments = () => fetchMoments().then(setMoments).catch(() => setMoments(null));
  useEffect(() => {
    void refreshMoments();
  }, []);
  const toggleQuietToday = async () => {
    if (!moments) return;
    try {
      await setMomentsSnoozed(!moments.snoozed_today);
      await refreshMoments();
      showSaved();
    } catch (err) {
      setPresenceError(err instanceof Error ? err.message : String(err));
    }
  };
  const dropWatch = async (id: string) => {
    try {
      await cancelMomentWatch(id);
      await refreshMoments();
    } catch (err) {
      setPresenceError(err instanceof Error ? err.message : String(err));
    }
  };
  const hourInput = (key: 'quiet_hours_start_local' | 'quiet_hours_end_local') => (
    <input
      type="number"
      min={0}
      max={23}
      value={presence?.[key] ?? 0}
      disabled={!presence}
      onChange={(e) => {
        const value = Number(e.target.value);
        if (Number.isInteger(value) && value >= 0 && value <= 23) void patchPresence({ [key]: value });
      }}
      className="w-16 px-2 py-1 rounded-lg text-sm text-center"
      style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
    />
  );
  const stamp = (at: number) =>
    new Date(at * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
  const numberInput = (
    key: 'initiative_idle_seconds' | 'initiative_cooldown_seconds' | 'initiative_per_hour',
    scale: number,
    min: number,
  ) => (
    <input
      type="number"
      min={min}
      value={presence ? Math.round(presence[key] / scale) : 0}
      disabled={!presence}
      onChange={(e) => {
        const value = Number(e.target.value);
        if (Number.isInteger(value) && value >= min) void patchPresence({ [key]: value * scale });
      }}
      className="w-16 px-2 py-1 rounded-lg text-sm text-center"
      style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
    />
  );
  const listInput = (key: 'initiative_excluded_facts' | 'initiative_call_titles', placeholder: string) => (
    <input
      type="text"
      defaultValue={(presence?.[key] ?? []).join(', ')}
      placeholder={placeholder}
      disabled={!presence}
      onBlur={(e) => {
        const value = e.target.value.split(',').map((s) => s.trim()).filter(Boolean);
        if (JSON.stringify(value) !== JSON.stringify(presence?.[key] ?? [])) void patchPresence({ [key]: value });
      }}
      className="w-64 px-2 py-1 rounded-lg text-sm"
      style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
    />
  );
  const quietFor = async (minutes: number) => {
    try {
      await setMomentsSnoozed(true, minutes);
      await refreshMoments();
      showSaved();
    } catch (err) {
      setPresenceError(err instanceof Error ? err.message : String(err));
    }
  };

  const [autoUpdateEnabled, setAutoUpdateEnabled] = useState(() => !isAutoUpdateDisabled());
  const [updateCheckState, setUpdateCheckState] = useState<'idle' | 'checking' | 'available' | 'latest'>('idle');

  const handleAutoUpdateToggle = useCallback((enabled: boolean) => {
    setAutoUpdateEnabled(enabled);
    setAutoUpdateDisabled(!enabled);
  }, []);

  const handleCheckNow = useCallback(async () => {
    if (!(window as any).__TAURI_INTERNALS__) return;
    setUpdateCheckState('checking');
    try {
      const { check } = await import('@tauri-apps/plugin-updater');
      const update = await check();
      setUpdateCheckState(update ? 'available' : 'latest');
      setTimeout(() => setUpdateCheckState('idle'), 4000);
    } catch {
      setUpdateCheckState('idle');
    }
  }, []);

  const [memoryStats, setMemoryStats] = useState<{ entries: number; backend: string } | null>(null);
  const [memoryEnabled, setMemoryEnabled] = useState(() => {
    try { return localStorage.getItem('openjarvis-memory-enabled') !== 'false'; } catch { return true; }
  });
  const [memoryBackend, setMemoryBackend] = useState(() => {
    try { return localStorage.getItem('openjarvis-memory-backend') || 'sqlite'; } catch { return 'sqlite'; }
  });
  const [memoryTopK, setMemoryTopK] = useState(() => {
    try { return parseInt(localStorage.getItem('openjarvis-memory-top-k') || '5'); } catch { return 5; }
  });
  const [memoryMinScore, setMemoryMinScore] = useState(() => {
    try { return parseFloat(localStorage.getItem('openjarvis-memory-min-score') || '0.1'); } catch { return 0.1; }
  });
  const [memoryMaxTokens, setMemoryMaxTokens] = useState(() => {
    try { return parseInt(localStorage.getItem('openjarvis-memory-max-tokens') || '2048'); } catch { return 2048; }
  });

  const [srcKind, setSrcKind] = useState<InferenceSource['kind']>('ollama');
  const [customHost, setCustomHost] = useState('http://localhost:1234/v1');
  const [customModel, setCustomModel] = useState('');
  const [customEngine, setCustomEngine] = useState('lmstudio');
  const [customKey, setCustomKey] = useState('');
  const [srcMsg, setSrcMsg] = useState('');

  useEffect(() => {
    getInferenceSource().then((s) => {
      setSrcKind(s.kind);
      if (s.host) setCustomHost(s.host);
      if (s.model) setCustomModel(s.model);
      if (s.engine) setCustomEngine(s.engine);
    }).catch(() => {});
  }, []);

  const saveSource = useCallback(async () => {
    try {
      if (srcKind === 'custom') {
        await setInferenceSource({ kind: 'custom', host: customHost, model: customModel, engine: customEngine, apiKey: customKey || undefined });
      } else {
        await setInferenceSource({ kind: 'ollama' });
      }
      setSrcMsg('Saved — restart the app to apply.');
    } catch (e: any) {
      setSrcMsg(e?.message ?? 'Failed to save.');
    }
  }, [srcKind, customHost, customModel, customEngine, customKey]);

  const refreshSpeechHealth = useCallback(() => {
    setSpeechChecking(true);
    fetchSpeechHealth()
      .then((h) => {
        setSpeechHealth(h);
        setSpeechBackendAvailable(h.available);
        setWakeWordAvailable(!!h.wake_word_available);
        setFluxAvailable(!!h.flux_available);
        setFluxReason(h.flux_reason || '');
      })
      .catch(() => {
        setSpeechHealth(null);
        setSpeechBackendAvailable(false);
        setWakeWordAvailable(false);
        setFluxAvailable(false);
        setFluxReason('server unreachable');
      })
      .finally(() => setSpeechChecking(false));
  }, []);

  useEffect(() => {
    checkHealth().then(setHealthy);
    refreshSpeechHealth();
    getMemoryStats()
      .then(setMemoryStats)
      .catch(() => setMemoryStats(null));
  }, [refreshSpeechHealth]);

  // Which of the three voice modes is actually in effect. Flux enabled but
  // unavailable reports the fallback rather than claiming the chosen mode.
  const parakeetAvailable = speechHealth?.stt?.parakeet?.available ?? null;
  const voiceModeLabel =
    settings.sttProvider === 'whisper'
      ? 'Local (faster-whisper)'
      : settings.sttProvider === 'parakeet'
        ? parakeetAvailable === false
          ? 'Local — Parakeet unavailable, using Whisper fallback'
          : 'Parakeet (local streaming)'
        : fluxAvailable === false
          ? 'Local — Flux unavailable, using fallback'
          : fluxAvailable === null
            ? 'Checking…'
            : settings.fluxEagerEnabled
              ? 'Flux Ultra (speculative)'
              : 'Flux Standard';

  const showSaved = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  const handleExport = () => {
    const data = localStorage.getItem('openjarvis-conversations') || '{}';
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `openjarvis-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImport = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        try {
          const data = JSON.parse(ev.target?.result as string);
          if (data.version === 1) {
            localStorage.setItem('openjarvis-conversations', JSON.stringify(data));
            useAppStore.getState().loadConversations();
            showSaved();
          }
        } catch {}
      };
      reader.readAsText(file);
    };
    input.click();
  };

  const [confirmClear, setConfirmClear] = useState(false);
  const handleClear = () => {
    if (!confirmClear) {
      setConfirmClear(true);
      setTimeout(() => setConfirmClear(false), 3000);
      return;
    }
    localStorage.removeItem('openjarvis-conversations');
    useAppStore.getState().loadConversations();
    setConfirmClear(false);
    showSaved();
  };

  return (
    <div className="flex-1 overflow-y-auto px-6 py-10">
      <div className="max-w-2xl mx-auto">
        <header className="mb-6">
          <div className="flex items-center justify-between gap-3">
            <h1 className="text-lg font-semibold" style={{ color: 'var(--color-text)' }}>
              Settings
            </h1>
            {saved && (
              <span className="flex items-center gap-1 text-xs px-2 py-1 rounded-full" style={{
                background: 'var(--color-accent-subtle)',
                color: 'var(--color-success)',
              }}>
                <Check size={12} /> Saved
              </span>
            )}
          </div>
          <p className="text-sm mt-2 max-w-2xl" style={{ color: 'var(--color-text-secondary)' }}>
            App preferences — appearance, model defaults, keyboard shortcuts, and data management.
          </p>
        </header>

        <div className="flex flex-col gap-4">
          {/* Appearance */}
          <Section title="Diagrams">
            <SettingRow
              label="Let Sage draw diagrams"
              description="Explanations of how something works, how it is made, what goes into it, or two options weighed up are drawn as a diagram over the app instead of sketched in text. Esc or Close dismisses it; a spoken one leaves when Sage stops talking."
            >
              <Switch
                on={settings.diagramsEnabled}
                onClick={() => { updateSettings({ diagramsEnabled: !settings.diagramsEnabled }); showSaved(); }}
              />
            </SettingRow>
            <SettingRow
              label="Decide on its own when to draw"
              description={
                settings.diagramsAutomatic
                  ? 'Sage draws one whenever the answer is a process, a structure or a set of parts.'
                  : 'Sage draws one only when you ask — "show me how", "illustrate that", "diagram it".'
              }
            >
              <Switch
                on={settings.diagramsAutomatic}
                disabled={!settings.diagramsEnabled}
                onClick={() => { updateSettings({ diagramsAutomatic: !settings.diagramsAutomatic }); showSaved(); }}
              />
            </SettingRow>
          </Section>

          <Section title="Appearance">
            <SettingRow label="Theme" description="Choose how OpenJarvis looks">
              <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--color-bg-secondary)' }}>
                {themeOptions.map((opt) => {
                  const isActive = settings.theme === opt.value;
                  return (
                    <button
                      key={opt.value}
                      onClick={() => { updateSettings({ theme: opt.value }); showSaved(); }}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer"
                      style={{
                        background: isActive ? 'var(--color-surface)' : 'transparent',
                        color: isActive ? 'var(--color-text)' : 'var(--color-text-tertiary)',
                        boxShadow: isActive ? 'var(--shadow-sm)' : 'none',
                      }}
                    >
                      <opt.icon size={14} />
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </SettingRow>
            <SettingRow
              label="Orb"
              description="The sphere Sage shows while it listens and speaks"
            >
              <select
                value={settings.orbDesign}
                onChange={(e) => {
                  updateSettings({ orbDesign: e.target.value as OrbDesign });
                  showSaved();
                }}
                className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <option value="constellation">Constellation</option>
                <option value="cloud">Particle cloud</option>
              </select>
            </SettingRow>
            {settings.orbDesign === 'constellation' && (
              <SettingRow
                label="Orb spikes"
                description="Short spikes that fan out from the orb when a pulse of speech travels through it. Off keeps the orb a clean sphere."
              >
                <Switch
                  on={settings.orbSpikes}
                  onClick={() => { updateSettings({ orbSpikes: !settings.orbSpikes }); showSaved(); }}
                />
              </SettingRow>
            )}
            <SettingRow label="Font size">
              <select
                value={settings.fontSize}
                onChange={(e) => { updateSettings({ fontSize: e.target.value as any }); showSaved(); }}
                className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <option value="small">Small</option>
                <option value="default">Default</option>
                <option value="large">Large</option>
              </select>
            </SettingRow>
          </Section>

          {/* Connection */}
          <Section title="Connection">
            <SettingRow label="Server status" description={serverInfo ? `${serverInfo.engine} / ${serverInfo.model}` : 'Not connected'}>
              <div className="flex items-center gap-2">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ background: healthy === true ? 'var(--color-success)' : healthy === false ? 'var(--color-error)' : 'var(--color-text-tertiary)' }}
                />
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {healthy === true ? 'Connected' : healthy === false ? 'Disconnected' : 'Checking...'}
                </span>
              </div>
            </SettingRow>
            <SettingRow label="API URL" description="Set if backend runs on a different port or host">
              <input
                type="text"
                value={settings.apiUrl}
                onChange={(e) => { updateSettings({ apiUrl: e.target.value }); showSaved(); }}
                placeholder="http://localhost:8000"
                className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              />
            </SettingRow>
            <SettingRow label="API key" description="Required only if the server was started with an API key">
              <input
                type="password"
                value={settings.apiKey}
                onChange={(e) => { updateSettings({ apiKey: e.target.value }); showSaved(); }}
                placeholder="OPENJARVIS_API_KEY"
                autoComplete="off"
                className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              />
            </SettingRow>
          </Section>

          {/* Inference source */}
          <Section title="Inference source">
            <SettingRow label="Source" description="Where the app runs models. Applies after restart.">
              <select
                value={srcKind}
                onChange={(e) => { setSrcKind(e.target.value as InferenceSource['kind']); setSrcMsg(''); }}
                className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
              >
                <option value="ollama">Bundled Ollama (default)</option>
                <option value="custom">Custom OpenAI-compatible server</option>
              </select>
            </SettingRow>
            {srcKind === 'custom' && (
              <>
                <SettingRow label="Server URL" description="e.g. LM Studio: http://localhost:1234/v1">
                  <input type="text" value={customHost} onChange={(e) => { setCustomHost(e.target.value); setSrcMsg(''); }} placeholder="http://localhost:1234/v1"
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }} />
                </SettingRow>
                <SettingRow label="Model" description="Model id served by your endpoint">
                  <input type="text" value={customModel} onChange={(e) => { setCustomModel(e.target.value); setSrcMsg(''); }} placeholder="qwen2.5-7b-instruct"
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }} />
                </SettingRow>
                <SettingRow label="Server type" description="OpenAI-compatible engine">
                  <select value={customEngine} onChange={(e) => { setCustomEngine(e.target.value); setSrcMsg(''); }}
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}>
                    <option value="lmstudio">LM Studio</option>
                    <option value="vllm">vLLM</option>
                    <option value="sglang">SGLang</option>
                    <option value="llamacpp">llama.cpp</option>
                    <option value="mlx">MLX</option>
                  </select>
                </SettingRow>
                <SettingRow label="API key (optional)" description="Only if your server requires one">
                  <input type="password" value={customKey} onChange={(e) => { setCustomKey(e.target.value); setSrcMsg(''); }} placeholder="leave blank if none"
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }} />
                </SettingRow>
              </>
            )}
            <SettingRow label="" description={srcMsg}>
              <button onClick={saveSource}
                className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
                style={{ background: 'var(--color-accent, var(--color-bg-tertiary))', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}>
                Save inference source
              </button>
            </SettingRow>
          </Section>

          {/* Models */}
          <Section title="Models">
            <SettingRow label="Prefer cloud model" description="Cloud answers roughly 6x faster at the prompt sizes Sage actually sends — 1.9s versus 11.7s on the same question. Falls back to your local model automatically when cloud is unavailable">
              <button
                onClick={() => {
                  const next = !settings.preferCloudModel;
                  updateSettings({ preferCloudModel: next });
                  const ids = models
                    .map((m) => m.id)
                    .filter((id) => !/embed/i.test(id));
                  const target = modelForToggle(
                    ids,
                    {
                      preferCloudModel: settings.preferCloudModel,
                      cloudModel: settings.cloudModel,
                      localModel: settings.defaultModel,
                      cloudAvailable: cloudModelAvailable,
                    },
                    next,
                  );
                  if (target) setSelectedModel(target);
                  showSaved();
                }}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: settings.preferCloudModel ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: settings.preferCloudModel ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Local models (Ollama)" description="Models available for local inference">
              <OllamaModelList />
            </SettingRow>
            <div className="text-xs mt-2 px-1" style={{ color: 'var(--color-text-tertiary)' }}>
              Run <code className="px-1 py-0.5 rounded text-[11px]" style={{ background: 'var(--color-bg-tertiary)' }}>ollama pull &lt;model-name&gt;</code> in your terminal to add more models
            </div>
            <SettingRow label="Cloud providers" description="Green dot means API key is configured">
              <div className="flex flex-wrap gap-3">
                <CloudProviderStatus label="OpenAI" keyName="OPENAI_API_KEY" />
                <CloudProviderStatus label="Anthropic" keyName="ANTHROPIC_API_KEY" />
                <CloudProviderStatus label="Google" keyName="GEMINI_API_KEY" />
                <CloudProviderStatus label="OpenRouter" keyName="OPENROUTER_API_KEY" />
              </div>
            </SettingRow>
          </Section>

          {/* API Keys */}
          <Section title="API Keys">
            <SettingRow label="OpenAI" description="GPT-4, GPT-3.5, etc.">
              <ApiKeyInput keyName="OPENAI_API_KEY" placeholder="sk-..." toolName="cloud_openai" />
            </SettingRow>
            <SettingRow label="Anthropic" description="Claude models">
              <ApiKeyInput keyName="ANTHROPIC_API_KEY" placeholder="sk-ant-..." toolName="cloud_anthropic" />
            </SettingRow>
            <SettingRow label="Google" description="Gemini models">
              <ApiKeyInput keyName="GEMINI_API_KEY" placeholder="AI..." toolName="cloud_google" />
            </SettingRow>
            <SettingRow label="OpenRouter" description="Multi-provider routing">
              <ApiKeyInput keyName="OPENROUTER_API_KEY" placeholder="sk-or-..." toolName="cloud_openrouter" />
            </SettingRow>
          </Section>

          {/* Tools */}
          <Section title="Tools">
            <SettingRow label="Web Search" description="Tavily key for web search tool">
              <ApiKeyInput keyName="TAVILY_API_KEY" placeholder="tvly-..." toolName="web_search" />
            </SettingRow>
          </Section>

          {/* Memory */}
          <Section title="Memory">
            <SettingRow label="Memory status" description={memoryStats ? `${memoryStats.backend} backend — ${memoryStats.entries} entries` : 'Unable to reach memory service'}>
              <div className="flex items-center gap-2">
                <Brain size={14} style={{ color: memoryStats ? 'var(--color-accent)' : 'var(--color-text-tertiary)' }} />
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {memoryStats ? `${memoryStats.entries} entries` : 'Unavailable'}
                </span>
              </div>
            </SettingRow>
            <SettingRow label="Use memory context" description="Automatically inject relevant memories into conversations">
              <button
                onClick={() => {
                  const next = !memoryEnabled;
                  setMemoryEnabled(next);
                  try { localStorage.setItem('openjarvis-memory-enabled', String(next)); } catch {}
                  showSaved();
                }}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: memoryEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: memoryEnabled ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Memory backend" description="Which retrieval engine to use">
              <select
                value={memoryBackend}
                onChange={(e) => {
                  setMemoryBackend(e.target.value);
                  try { localStorage.setItem('openjarvis-memory-backend', e.target.value); } catch {}
                  showSaved();
                }}
                className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <option value="sqlite">sqlite</option>
                <option value="faiss">faiss</option>
                <option value="bm25">bm25</option>
                <option value="colbert">colbert</option>
                <option value="hybrid">hybrid</option>
              </select>
            </SettingRow>
            <SettingRow label="Results to inject" description={`${memoryTopK}`}>
              <input
                type="range"
                min="1"
                max="20"
                step="1"
                value={memoryTopK}
                onChange={(e) => {
                  const v = parseInt(e.target.value);
                  setMemoryTopK(v);
                  try { localStorage.setItem('openjarvis-memory-top-k', String(v)); } catch {}
                  showSaved();
                }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
            <SettingRow label="Min relevance score" description={`${memoryMinScore}`}>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={memoryMinScore}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  setMemoryMinScore(v);
                  try { localStorage.setItem('openjarvis-memory-min-score', String(v)); } catch {}
                  showSaved();
                }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
            <SettingRow label="Max context tokens" description={`${memoryMaxTokens}`}>
              <input
                type="range"
                min="256"
                max="8192"
                step="256"
                value={memoryMaxTokens}
                onChange={(e) => {
                  const v = parseInt(e.target.value);
                  setMemoryMaxTokens(v);
                  try { localStorage.setItem('openjarvis-memory-max-tokens', String(v)); } catch {}
                  showSaved();
                }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
          </Section>

          {/* Model defaults */}
          <Section title="Model Defaults">
            <SettingRow label="Temperature" description={`${settings.temperature}`}>
              <input
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={settings.temperature}
                onChange={(e) => { updateSettings({ temperature: parseFloat(e.target.value) }); showSaved(); }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
            <SettingRow label="Max tokens" description={`${settings.maxTokens}`}>
              <input
                type="range"
                min="256"
                max="32768"
                step="256"
                value={settings.maxTokens}
                onChange={(e) => { updateSettings({ maxTokens: parseInt(e.target.value) }); showSaved(); }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
          </Section>

          {/* Volume */}
          <Section title="Volume">
            <p className="text-xs mb-3" style={{ color: 'var(--color-text-secondary)' }}>
              How loud Sage is, separately from Windows. Master scales every channel; each channel is a share of it. Shared by every tab and by the voices the server plays itself.
              {volumeError ? ` (${volumeError})` : ''}
            </p>
            {VOLUME_ROWS.map(([key, label, description]) => (
              <SettingRow key={key} label={label} description={`${Math.round((volumes?.[key] ?? 1) * 100)}% — ${description}`}>
                <input
                  type="range"
                  min="0"
                  max="100"
                  step="5"
                  value={Math.round((volumes?.[key] ?? 1) * 100)}
                  disabled={!volumes}
                  onChange={(e) => { void patchVolume({ [key]: parseInt(e.target.value) / 100 }); }}
                  className="w-32 cursor-pointer accent-[var(--color-accent)]"
                />
              </SettingRow>
            ))}
          </Section>

          {/* Speech */}
          <Section title="Microphone">
            <SettingRow
              label="Remove background noise"
              description={
                settings.noiseSuppression === 'all'
                  ? 'Everything, including the wake word. Try this when the room itself is the problem — a laptop fan beside the mic. Be aware the wake word may fire less reliably: its detector was trained on raw audio, and with suppression on, noise, keyboard clicks and speech scored alike in testing.'
                  : settings.noiseSuppression === 'off'
                    ? 'Off — Sage hears the room exactly as the microphone does.'
                    : 'Steady noise like a fan is stripped from what you say to Sage. The wake word still hears raw audio, which is what its detector was tuned on.'
              }
            >
              <select
                aria-label="Remove background noise"
                value={settings.noiseSuppression}
                onChange={(event) => {
                  updateSettings({
                    noiseSuppression: event.target
                      .value as typeof settings.noiseSuppression,
                  });
                  showSaved();
                }}
                className="px-2 py-1 rounded-lg text-sm"
                style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
              >
                <option value="conversation">What I say to Sage</option>
                <option value="all">Everything, wake word too</option>
                <option value="off">Off</option>
              </select>
            </SettingRow>
            <SettingRow
              label="Extra boost"
              description={
                settings.micBoost > 1
                  ? `${settings.micBoost.toFixed(1)}x on top of the automatic level. Sage already matches a quiet microphone up to a ceiling on its own; add to this only when you are far from a laptop mic and still not heard.`
                  : 'Sage matches a quiet microphone to a usable level on its own. Add extra only if you are far from a laptop mic and still not heard — it lifts room noise too.'
              }
            >
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  aria-label="Extra microphone boost"
                  min={1}
                  max={4}
                  step={0.5}
                  value={settings.micBoost}
                  onChange={(event) => {
                    updateSettings({ micBoost: Number(event.target.value) });
                    showSaved();
                  }}
                />
                <span
                  className="text-xs font-mono w-10 text-right"
                  style={{ color: 'var(--color-text-secondary)' }}
                >
                  {settings.micBoost.toFixed(1)}x
                </span>
              </div>
            </SettingRow>
          </Section>

          <Section title="Words to listen for">
            <KeytermEditor showSaved={showSaved} />
          </Section>

          <Section title="Speech">
            <VoiceProviders
              health={speechHealth}
              checking={speechChecking}
              onRefresh={refreshSpeechHealth}
              onSaved={showSaved}
              Row={SettingRow}
              Switch={Switch}
            />
            <SettingRow label="Speech-to-Text" description="Enable microphone input for voice dictation">
              <button
                onClick={() => { updateSettings({ speechEnabled: !settings.speechEnabled }); showSaved(); }}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: settings.speechEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: settings.speechEnabled ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Backend status" description="Requires Whisper, Deepgram, or another speech backend">
              <div className="flex items-center gap-2">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{
                    background: speechBackendAvailable === true ? 'var(--color-success)'
                      : speechBackendAvailable === false ? 'var(--color-text-tertiary)'
                      : 'var(--color-text-tertiary)',
                  }}
                />
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {speechBackendAvailable === null ? 'Checking...'
                    : speechBackendAvailable ? 'Available'
                    : 'Not configured'}
                </span>
              </div>
            </SettingRow>
            {wakeWordAvailable && (
              <>
                <SettingRow label={'Wake Word ("Hey Sage")'} description="Listen continuously and start recording when you say the wake word">
                  <button
                    onClick={() => { updateSettings({ wakeWordEnabled: !settings.wakeWordEnabled }); showSaved(); }}
                    className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                    style={{
                      background: settings.wakeWordEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                    }}
                  >
                    <span
                      className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                      style={{
                        transform: settings.wakeWordEnabled ? 'translateX(20px)' : 'translateX(0)',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                      }}
                    />
                  </button>
                </SettingRow>
                <SettingRow label="Greet on Wake Word" description='Say "Hello, sir" before listening, so a trigger is audible. Turn off to start listening silently — also useful when recording wake-word training samples'>
                  <button
                    onClick={() => { updateSettings({ wakeWordGreetingEnabled: !settings.wakeWordGreetingEnabled }); showSaved(); }}
                    className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                    style={{
                      background: settings.wakeWordGreetingEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                    }}
                  >
                    <span
                      className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                      style={{
                        transform: settings.wakeWordGreetingEnabled ? 'translateX(20px)' : 'translateX(0)',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                      }}
                    />
                  </button>
                </SettingRow>
                <SettingRow label="Wake word check" description="Before Sage answers a wake word it transcribes the last two seconds with a small model kept ready on this machine (about a tenth of a second) and must hear the phrase, so a loud noise or the TV cannot wake it. Off trusts the detector alone. Ignored firings show in the Voice log with what was heard.">
                  <select
                    value={settings.wakeWordVerify}
                    onChange={(e) => { updateSettings({ wakeWordVerify: e.target.value as WakeWordVerify }); showSaved(); }}
                    className="px-2 py-1 rounded-lg text-sm"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
                  >
                    <option value="local">On</option>
                    <option value="off">Off</option>
                  </select>
                </SettingRow>
                <SettingRow label="Speak right after the wake word" description='Say "Hey Sage, any news on AI?" in one breath: Sage keeps what you said from the wake word on and skips "Yes, Sir?" unless you pause for a second. Flux mode only.'>
                  <Switch on={settings.wakeWordFastFollow} onClick={() => { updateSettings({ wakeWordFastFollow: !settings.wakeWordFastFollow }); showSaved(); }} />
                </SettingRow>
                <SettingRow label="Listen after wake word" description={`${settings.wakeWordListenSeconds} s — how long the microphone stays open after "Hey Sage" if you say nothing.`}>
                  <input
                    type="range"
                    min={LISTEN_SECONDS_MIN}
                    max={LISTEN_SECONDS_MAX}
                    step="1"
                    value={settings.wakeWordListenSeconds}
                    onChange={(e) => { updateSettings({ wakeWordListenSeconds: parseInt(e.target.value) }); showSaved(); }}
                    className="w-32 cursor-pointer accent-[var(--color-accent)]"
                  />
                </SettingRow>
                <SettingRow label="Local Vision Model" description={`Answer image questions with ${settings.visionLocalModel} on this machine instead of the cloud model. Slower and a weaker reasoner, but the picture never leaves your computer`}>
                  <button
                    onClick={() => { updateSettings({ visionUseLocal: !settings.visionUseLocal }); showSaved(); }}
                    className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                    style={{
                      background: settings.visionUseLocal ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                    }}
                  >
                    <span
                      className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                      style={{
                        transform: settings.visionUseLocal ? 'translateX(20px)' : 'translateX(0)',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                      }}
                    />
                  </button>
                </SettingRow>
                <SettingRow label="Speak Replies" description="Read answers aloud when you asked by voice. Streamed speech has no player, so this and the stop button beside the composer are the only ways to silence a reply">
                  <button
                    onClick={() => { updateSettings({ voiceRepliesEnabled: !settings.voiceRepliesEnabled }); showSaved(); }}
                    className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                    style={{
                      background: settings.voiceRepliesEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                    }}
                  >
                    <span
                      className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                      style={{
                        transform: settings.voiceRepliesEnabled ? 'translateX(20px)' : 'translateX(0)',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                      }}
                    />
                  </button>
                </SettingRow>
                <SettingRow label="Interrupt by speaking" description="Talk over a spoken reply and Sage stops to listen. One clear 'stop', 'wait' or 'hold on' cuts at once; otherwise it takes a few words Sage is sure of. Sage's own voice and words it is unsure of never cut. Voice questions in Flux mode only; typed replies are not cut.">
                  <Switch on={settings.bargeInEnabled} onClick={() => { updateSettings({ bargeInEnabled: !settings.bargeInEnabled }); showSaved(); }} />
                </SettingRow>
                <SettingRow label="Interruption sensitivity" description="Conservative: three confident words. Balanced: two. Sensitive: two, or one very sure word. Start conservative; move up only if Sage talks over you.">
                  <select
                    value={settings.bargeInMode}
                    disabled={!settings.bargeInEnabled}
                    onChange={(e) => { updateSettings({ bargeInMode: e.target.value as BargeMode }); showSaved(); }}
                    className="px-2 py-1 rounded-lg text-sm"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
                  >
                    <option value="conservative">Conservative</option>
                    <option value="balanced">Balanced</option>
                    <option value="sensitive">Sensitive</option>
                  </select>
                </SettingRow>
                <SettingRow label="Speak Typed Replies" description={`Also read answers aloud when you typed the question, not just when you spoke it. Code blocks are skipped and very long answers are cut short, because neither is listenable.${settings.voiceRepliesEnabled ? '' : ' Currently silent: Speak Replies above is off, and it overrides this.'}`}>
                  <button
                    onClick={() => { updateSettings({ speakTypedReplies: !settings.speakTypedReplies }); showSaved(); }}
                    className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                    style={{
                      background: settings.speakTypedReplies ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                      opacity: settings.voiceRepliesEnabled ? 1 : 0.5,
                    }}
                  >
                    <span
                      className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                      style={{
                        transform: settings.speakTypedReplies ? 'translateX(20px)' : 'translateX(0)',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                      }}
                    />
                  </button>
                </SettingRow>
                <SettingRow label="Listen for a follow-up" description={`${settings.continuousListenSeconds} s — with Continuous Conversation on, how long the microphone stays open after a reply for your next turn.`}>
                  <input
                    type="range"
                    min={LISTEN_SECONDS_MIN}
                    max={LISTEN_SECONDS_MAX}
                    step="1"
                    value={settings.continuousListenSeconds}
                    disabled={!settings.continuousConversationEnabled}
                    onChange={(e) => { updateSettings({ continuousListenSeconds: parseInt(e.target.value) }); showSaved(); }}
                    className="w-32 cursor-pointer accent-[var(--color-accent)]"
                  />
                </SettingRow>
                <SettingRow label="Continuous Conversation" description="After Sage replies, automatically listen for your next turn instead of requiring the wake word again">
                  <button
                    onClick={() => { updateSettings({ continuousConversationEnabled: !settings.continuousConversationEnabled }); showSaved(); }}
                    className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                    style={{
                      background: settings.continuousConversationEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                    }}
                  >
                    <span
                      className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                      style={{
                        transform: settings.continuousConversationEnabled ? 'translateX(20px)' : 'translateX(0)',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                      }}
                    />
                  </button>
                </SettingRow>
                <SettingRow
                  label="Ultra-low-latency voice"
                  description="Start answering before Deepgram confirms you finished, and discard that work if you keep talking. Speculative answers are never spoken, shown, or allowed to run tools before confirmation. May start extra cloud LLM requests that are thrown away, increasing usage."
                >
                  <button
                    disabled={settings.sttProvider !== 'flux' || fluxAvailable === false}
                    onClick={() => {
                      // Speculation is Flux's; Parakeet has no eager end of turn.
                      if (settings.sttProvider !== 'flux') return;
                      updateSettings({ fluxEagerEnabled: !settings.fluxEagerEnabled });
                      showSaved();
                    }}
                    className="relative w-11 h-6 rounded-full transition-colors"
                    style={{
                      background: settings.fluxEagerEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                      cursor: settings.sttProvider !== 'flux' || fluxAvailable === false ? 'not-allowed' : 'pointer',
                      opacity: settings.sttProvider !== 'flux' || fluxAvailable === false ? 0.5 : 1,
                    }}
                  >
                    <span
                      className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                      style={{
                        transform: settings.fluxEagerEnabled ? 'translateX(20px)' : 'translateX(0)',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                      }}
                    />
                  </button>
                </SettingRow>
                <div className="text-xs mt-1 px-1" style={{ color: 'var(--color-text-tertiary)' }}>
                  Transcription:{' '}
                  <span style={{ color: 'var(--color-accent)' }}>{voiceModeLabel}</span>
                </div>
              </>
            )}
            {!speechBackendAvailable && speechBackendAvailable !== null && (
              <div className="text-xs mt-2 px-1" style={{ color: 'var(--color-text-tertiary)' }}>
                Set up a speech backend to use voice input.
                See the <a href="https://open-jarvis.github.io/OpenJarvis/user-guide/tools/" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-accent)' }}>documentation</a> for details.
              </div>
            )}
          </Section>

          {/* Presence (M36) */}
          <Section title="Presence">
            <SettingRow label="Sage knows when you are here" description={`Lets Sage tell whether anyone is at the desk, from keyboard and mouse activity and the window in front. This is the master switch for everything Sage does on its own; off, it behaves exactly as before. What it currently believes is shown on the Health page.${presenceError ? ` (${presenceError})` : ''}`}>
              <Switch on={Boolean(presence?.enabled)} onClick={togglePresence} disabled={!presence} />
            </SettingRow>
            <SettingRow label="Sage may speak first" description="Out loud through the speakers, only while you are at the desk and never during quiet hours. Each occasion below has its own switch.">
              <Switch on={Boolean(presence?.moments_enabled)} onClick={flip('moments_enabled')} disabled={!presence?.enabled} />
            </SettingRow>
            <SettingRow label="Daily greeting" description="Once a day, the first time Sage sees you: good morning, afternoon or evening, with what you were on yesterday and what today holds.">
              <Switch on={Boolean(presence?.greeting_enabled)} onClick={flip('greeting_enabled')} disabled={!presence?.enabled || !presence?.moments_enabled} />
            </SettingRow>
            <SettingRow label="Welcome back" description={`When you return after at least ${Math.round((presence?.welcome_back_after_seconds ?? 3600) / 60)} minutes away (Sage off in between counts), with anything that finished while you were gone.`}>
              <Switch on={Boolean(presence?.welcome_back_enabled)} onClick={flip('welcome_back_enabled')} disabled={!presence?.enabled || !presence?.moments_enabled} />
            </SettingRow>
            <SettingRow label="Tell me when" description="Things you asked to be told about, spoken when they happen. Ask in chat or by voice: tell me when my class starts.">
              <Switch on={Boolean(presence?.told_enabled)} onClick={flip('told_enabled')} disabled={!presence?.enabled || !presence?.moments_enabled} />
            </SettingRow>
            <SettingRow label="Quiet hours" description="Local time. Nothing is said first between these hours, however good the reason.">
              <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                {hourInput('quiet_hours_start_local')} <span>to</span> {hourInput('quiet_hours_end_local')}
              </div>
            </SettingRow>
            <SettingRow label="Not now" description={moments?.snoozed_today ? 'Quiet for the rest of today. Telling Sage "not now" does the same.' : 'Silence every unprompted moment until tomorrow. Telling Sage "not now" does the same.'}>
              <Switch on={Boolean(moments?.snoozed_today)} onClick={toggleQuietToday} disabled={!moments} />
            </SettingRow>
            <SettingRow label="Quiet for a while" description={moments?.snoozed_until ? `Quiet until ${stamp(moments.snoozed_until)}. "Continue" lifts it.` : 'Nothing unprompted for a set time. "Be quiet for 30 minutes" does the same; reminders you scheduled still speak.'}>
              <div className="flex gap-1">
                {[30, 60, 120].map((m) => (
                  <button key={m} onClick={() => quietFor(m)} disabled={!moments} className="px-2 py-1 rounded-lg text-xs cursor-pointer disabled:opacity-40" style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}>
                    {m < 60 ? `${m} min` : `${m / 60} h`}
                  </button>
                ))}
              </div>
            </SettingRow>
            <SettingRow label="Sage may start conversations" description="Initiative: after a lull, Sage may ask about today's work or offer a useful nudge -- or decide silence is better. Gentle stays with today's work; Curious adds questions and facts from civil engineering, AI and Sage, and whatever you've been talking about lately; Social adds observations about how you work and may bring up anything it remembers that isn't on the never-bring-up list.">
              <select
                value={presence?.initiative_mode ?? 'off'}
                disabled={!presence?.enabled || !presence?.moments_enabled}
                onChange={(e) => void patchPresence({ initiative_mode: e.target.value as PresenceSettings['initiative_mode'] })}
                className="px-2 py-1 rounded-lg text-sm"
                style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
              >
                <option value="off">Off</option>
                <option value="gentle">Gentle</option>
                <option value="curious">Curious</option>
                <option value="social">Social</option>
              </select>
            </SettingRow>
            <SettingRow label="Lull, cooldown, per hour" description="Minutes of quiet on both sides before Sage considers speaking; minutes between two initiatives; the most in one hour. Picking a mode sets these to its preset (Gentle 5/10/3, Curious 4/7/5, Social 3/5/8); edit freely after. Declining counts as half a cooldown.">
              <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                {numberInput('initiative_idle_seconds', 60, 1)} <span>min</span>
                {numberInput('initiative_cooldown_seconds', 60, 1)} <span>min</span>
                {numberInput('initiative_per_hour', 1, 1)} <span>/h</span>
              </div>
            </SettingRow>
            <SettingRow label="Never bring up" description="Comma-separated words. A remembered fact containing any of them is never shown to the initiative writer. Add one when a prompt touched something it shouldn't have.">
              {listInput('initiative_excluded_facts', 'e.g. ex, clinic')}
            </SettingRow>
            <SettingRow label="Busy when the window in front says" description="Comma-separated words in a window title that mean you're in a call. A full-screen app, and Teams holding the microphone, count as busy too.">
              {listInput('initiative_call_titles', 'Meet, Messenger call')}
            </SettingRow>
            {moments && moments.watches.length > 0 && (
              <div className="py-3" style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>
                <div className="text-sm mb-1" style={{ color: 'var(--color-text)' }}>Pending</div>
                {moments.watches.map((w) => (
                  <div key={w.id} className="flex items-center justify-between text-xs py-1" style={{ color: 'var(--color-text-secondary)' }}>
                    <span>
                      {w.what}: {w.due_at ? stamp(w.due_at) : `when task ${w.task_id} finishes`}
                    </span>
                    <button onClick={() => dropWatch(w.id)} className="cursor-pointer" style={{ color: 'var(--color-text-tertiary)' }}>
                      Cancel
                    </button>
                  </div>
                ))}
              </div>
            )}
            {moments && moments.history.length > 0 && (
              <div className="py-3">
                <div className="text-sm mb-1" style={{ color: 'var(--color-text)' }}>Recently said</div>
                {moments.history.slice(-5).reverse().map((h) => (
                  <div key={h.at} className="text-xs py-1" style={{ color: 'var(--color-text-secondary)' }}>
                    <span style={{ color: 'var(--color-text-tertiary)' }}>
                      {stamp(h.at)}{h.spoken ? '' : ' (not spoken)'}{h.detail.includes('category=') ? ` · ${h.detail.split('category=')[1].split(';')[0]}` : ''}:{' '}
                    </span>
                    {h.text}
                  </div>
                ))}
              </div>
            )}
          </Section>

          {/* Data */}
          <Section title="Data">
            <SettingRow label="Conversations" description={`${conversations.length} stored locally`}>
              <div className="flex gap-2">
                <button
                  onClick={handleExport}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
                  style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-bg-tertiary)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                >
                  <Download size={12} /> Export
                </button>
                <button
                  onClick={handleImport}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
                  style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-bg-tertiary)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                >
                  <Upload size={12} /> Import
                </button>
              </div>
            </SettingRow>
            <SettingRow label="Clear all data" description="Permanently delete all conversations">
              <button
                onClick={handleClear}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
                style={{
                  color: confirmClear ? 'white' : 'var(--color-error)',
                  background: confirmClear ? 'var(--color-error)' : 'transparent',
                  border: '1px solid var(--color-error)',
                }}
                onMouseEnter={(e) => { if (!confirmClear) e.currentTarget.style.background = 'rgba(220,38,38,0.1)'; }}
                onMouseLeave={(e) => { if (!confirmClear) e.currentTarget.style.background = 'transparent'; }}
              >
                <Trash2 size={12} /> {confirmClear ? 'Click again to confirm' : 'Clear'}
              </button>
            </SettingRow>
          </Section>

          {/* Updates */}
          <Section title="Updates">
            <SettingRow label="Auto-update" description="Check for new desktop builds automatically every 30 minutes">
              <button
                onClick={() => handleAutoUpdateToggle(!autoUpdateEnabled)}
                className="relative inline-flex h-5 w-9 items-center rounded-full transition-colors"
                style={{ background: autoUpdateEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)' }}
              >
                <span
                  className="inline-block h-3.5 w-3.5 rounded-full transition-transform"
                  style={{
                    background: 'white',
                    transform: autoUpdateEnabled ? 'translateX(18px)' : 'translateX(2px)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Check for updates" description="Manually check for a new version right now">
              <button
                onClick={handleCheckNow}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
                style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)', color: 'var(--color-text)', cursor: 'pointer' }}
                disabled={updateCheckState === 'checking'}
              >
                <RefreshCw size={12} className={updateCheckState === 'checking' ? 'animate-spin' : ''} />
                {updateCheckState === 'checking' && 'Checking...'}
                {updateCheckState === 'available' && 'Update available — see banner above'}
                {updateCheckState === 'latest' && 'Already up to date'}
                {updateCheckState === 'idle' && 'Check now'}
              </button>
            </SettingRow>
          </Section>

          {/* About */}
          <Section title="About">
            <div className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
              <p className="mb-2">
                <span className="font-semibold" style={{ color: 'var(--color-text)' }}>OpenJarvis</span> — Programming abstractions for on-device AI.
              </p>
              <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                Part of Intelligence Per Watt, a research initiative at Stanford SAIL.
              </p>
              <div className="flex gap-3 mt-3 text-xs">
                <a
                  href="https://openjarvis.stanford.edu/"
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: 'var(--color-accent)' }}
                >
                  Project site
                </a>
                <a
                  href="https://open-jarvis.github.io/OpenJarvis/"
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: 'var(--color-accent)' }}
                >
                  Documentation
                </a>
              </div>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

/**
 * Words Deepgram should expect to hear.
 *
 * Shown as plain lines rather than chips because the user is pasting names
 * and places, and a chip editor turns a paste of five names into five
 * separate gestures. What Sage already covers is listed underneath, greyed,
 * so nobody retypes "Quezon" or their own instructors.
 */
function KeytermEditor({ showSaved }: { showSaved: () => void }) {
  const [data, setData] = useState<Keyterms | null>(null);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchKeyterms()
      .then((loaded) => {
        setData(loaded);
        setText(loaded.terms.join('\n'));
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const save = useCallback(() => {
    setSaving(true);
    setError('');
    saveKeyterms(parseTerms(text))
      .then((saved) => {
        setText(saved.join('\n'));
        setData((prev) => (prev ? { ...prev, terms: saved } : prev));
        showSaved();
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setSaving(false));
  }, [text, showSaved]);

  const covered = data ? [...data.built_in, ...data.harvested] : [];

  return (
    <div className="flex flex-col gap-3 py-3">
      <div className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
        Names, places and phrases Sage should expect. Without them Deepgram
        heard “Hey Sage” as “addition”. One per line
        {data ? `, at least ${data.min_length} letters, up to ${data.max_terms} in total` : ''}.
      </div>
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={6}
        spellCheck={false}
        placeholder={'Revilloza\nCalamba\nkumusta'}
        className="w-full rounded-lg px-3 py-2 text-sm font-mono"
        style={{
          background: 'var(--color-input-bg)',
          color: 'var(--color-text)',
          border: '1px solid var(--color-input-border)',
          resize: 'vertical',
        }}
      />
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={saving || !data}
          className="px-4 py-2 rounded-lg text-sm font-medium cursor-pointer disabled:opacity-50"
          style={{ background: 'var(--color-accent)', color: 'var(--color-on-accent)' }}
        >
          {saving ? 'Saving…' : 'Save words'}
        </button>
        {error ? (
          <span className="text-xs" style={{ color: 'var(--color-error)' }}>{error}</span>
        ) : null}
      </div>
      {covered.length > 0 ? (
        <div className="text-xs leading-relaxed" style={{ color: 'var(--color-text-tertiary)' }}>
          Already covered: {covered.join(' · ')}
        </div>
      ) : null}
    </div>
  );
}
