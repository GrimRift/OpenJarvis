/**
 * Pairing a phone or iPad (M29, server/pairing.py): the PC asks for a
 * six-digit code, the phone sends it back and receives Sage's key.
 */
import { apiFetch, getApiKey, getBase } from './api';

export interface PairingCode {
  code: string;
  expiresIn: number;
  /** This PC's HTTPS address on the tailnet, "" without Tailscale. */
  address: string;
}

/** Settings that belong to this PC alone: its address and key, its
 * microphones and their gain and filtering, its screen's font size. */
const PC_ONLY = new Set([
  'apiUrl',
  'apiKey',
  'micBoost',
  'micDeviceId',
  'micDeviceLabel',
  'wakeMicDeviceId',
  'wakeMicDeviceLabel',
  'noiseSuppression',
  'fontSize',
  // The phone's own (M29): the PC has no say in them.
  'touchWakeWord',
  'touchFollowUp',
]);

/** Modes that suit a PC left listening on a desk, not a phone in a pocket:
 * a phone starts with them off, whatever the PC has. */
const PHONE_OFF = { continuousConversationEnabled: false, listenWhileWorking: false };

/** What a paired phone takes over from the PC: everything else. */
export function settingsForPhone(settings: object): Record<string, unknown> {
  const kept = Object.fromEntries(Object.entries(settings).filter(([key]) => !PC_ONLY.has(key)));
  return Object.keys(kept).length ? { ...kept, ...PHONE_OFF } : kept;
}

/** Marks a phone as paired with the PC's settings, not just its key. A phone
 * paired before the settings came along pairs once more. */
const PAIRED_FLAG = 'sage-device-paired';

export function devicePaired(): boolean {
  try {
    return localStorage.getItem(PAIRED_FLAG) === '2';
  } catch {
    return true;
  }
}

export function markDevicePaired(): void {
  try {
    localStorage.setItem(PAIRED_FLAG, '2');
  } catch {
    // Private mode: it pairs again next time.
  }
}

export async function newPairingCode(settings: object): Promise<PairingCode> {
  const res = await apiFetch('/v1/pairing/code', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: settingsForPhone(settings) }),
  });
  if (!res.ok) throw new Error(`Could not make a code (${res.status})`);
  const body = await res.json();
  return { code: String(body.code), expiresIn: Number(body.expires_in) || 300, address: String(body.address || '') };
}

export async function cancelPairingCode(): Promise<void> {
  await apiFetch('/v1/pairing/code', { method: 'DELETE' }).catch(() => {});
}

export type RedeemResult =
  | { ok: true; apiKey: string; settings: Record<string, unknown> }
  | { ok: false; message: string };

/** Sends the code typed on this device; no key yet, so a plain fetch. */
export async function redeemPairingCode(code: string): Promise<RedeemResult> {
  let res: Response;
  try {
    res = await fetch(`${getBase()}/v1/pairing/redeem`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: digitsOf(code) }),
    });
  } catch {
    return { ok: false, message: "Can't reach Sage's PC. Is it on, with Tailscale on here too?" };
  }
  if (res.ok) {
    const body = await res.json();
    const settings = body.settings && typeof body.settings === 'object' ? body.settings : {};
    return { ok: true, apiKey: String(body.api_key || ''), settings: settingsForPhone(settings) };
  }
  if (res.status === 403) return { ok: false, message: 'That code is not right. Check it and try again.' };
  return { ok: false, message: 'That code has run out. Make a new one in Settings on the PC.' };
}

/** The digits of what was typed, at most six. */
export function digitsOf(typed: string): string {
  return typed.replace(/\D/g, '').slice(0, 6);
}

/** "123456" -> "123 456", easier to read across a room. */
export function spacedCode(code: string): string {
  const d = digitsOf(code);
  return d.length > 3 ? `${d.slice(0, 3)} ${d.slice(3)}` : d;
}

/** "4:05" for a countdown in seconds. */
export function countdown(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const LAST_CHECK = 'sage-last-key-check';

/** The last start-up check's result, kept for the next pairing's trace. */
export function lastKeyCheck(): string {
  try {
    return localStorage.getItem(LAST_CHECK) || 'none';
  } catch {
    return 'unknown';
  }
}

function rememberKeyCheck(result: string): void {
  try {
    localStorage.setItem(LAST_CHECK, `${result}@${new Date().toISOString().slice(11, 19)} key=${getApiKey() ? 'yes' : 'no'}`);
  } catch {
    // Diagnostics only.
  }
}

/** How long a phone waits for Sage before saying it cannot reach it. */
export const KEY_CHECK_TIMEOUT_MS = 5000;

/** Whether this device can talk to Sage: 'ok', 'refused' (401: no key or a
 * wrong one), or 'unreachable' (no answer within KEY_CHECK_TIMEOUT_MS). */
export async function keyCheck(): Promise<'ok' | 'refused' | 'unreachable'> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), KEY_CHECK_TIMEOUT_MS);
  try {
    const res = await apiFetch('/v1/models', { signal: controller.signal });
    const result = res.status === 401 ? 'refused' : res.ok ? 'ok' : 'unreachable';
    rememberKeyCheck(result);
    return result;
  } catch {
    rememberKeyCheck('unreachable');
    return 'unreachable';
  } finally {
    clearTimeout(timer);
  }
}
