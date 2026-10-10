/**
 * The llama.cpp router's editable model settings (server: engine/llamacpp_presets.py).
 * Values arrive as the presets file's strings; null means "not set".
 */
import { apiFetch } from './api';

export interface LocalModelSettings {
  shared: Record<string, string | null>;
  models: Record<string, Record<string, string | null>>;
  reloaded?: boolean;
}

export async function fetchLocalModelSettings(): Promise<LocalModelSettings> {
  const res = await apiFetch('/v1/settings/local-models');
  if (!res.ok) throw new Error(`Could not read local model settings (${res.status})`);
  return res.json();
}

export async function saveLocalModelSettings(
  changes: Partial<Pick<LocalModelSettings, 'shared' | 'models'>>,
): Promise<LocalModelSettings> {
  const res = await apiFetch('/v1/settings/local-models', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json();
}

/** What each setting does and what changing it risks, shown under it. */
export const LOCAL_SETTING_HELP: Record<string, { label: string; help: string }> = {
  'sleep-idle-seconds': {
    label: 'Unload after idle (seconds)',
    help:
      'How long a local model stays loaded after its last reply. Longer: the next reply starts at once, ' +
      'but the model keeps holding graphics memory (games, the local voice and video get less). ' +
      'Shorter: memory frees sooner, but the next reply waits 5-10 s for the model to load again.',
  },
  'ctx-size': {
    label: 'Context size (tokens)',
    help:
      'How much text the model holds at once: Sage’s instructions, tools, memory and the conversation. A Sage ' +
      'turn is 10,000-12,600 tokens, so below about 14,000 replies fail with “exceeds the available context”. ' +
      'Higher fits longer chats but uses more graphics memory; when the card overflows, replies drop from ' +
      '~40 to ~6-20 tokens/s (measured on the 9B with the app open).',
  },
  'cache-type': {
    label: 'Conversation cache precision',
    help:
      'How precisely the model remembers the text it has read. q4_0 uses the least graphics memory (it is what ' +
      'lets the 9B run at ~40 tokens/s with a 16k context here) but answers about long text can be slightly less ' +
      'accurate. q8_0 is near-lossless but twice the memory; on the 9B it overflowed the card (~21 tokens/s). ' +
      'f16 (default): most precise, four times q4_0.',
  },
  'n-gpu-layers': {
    label: 'Layers on the graphics card',
    help:
      'How much of the model runs on the graphics card (99 = all of it). Lower frees graphics memory for ' +
      'other apps, but every layer moved to the CPU makes replies much slower: with 40% on the CPU the 9B ' +
      'measured ~10 tokens/s instead of ~50.',
  },
  'n-cpu-moe': {
    label: 'Expert layers kept in RAM',
    help:
      'For the 35B only: how many of its 40 layers keep their experts in system RAM instead of on the card. ' +
      'Lower is faster but needs more graphics memory; 22 overflowed the card and fell to ~5 tokens/s. ' +
      'Higher is safer but slower, and needs more free RAM.',
  },
};
