/**
 * Pairing a phone or iPad (M29, server/pairing.py): the PC asks for a
 * six-digit code, the phone sends it back and receives Sage's key.
 */
import { apiFetch, getBase } from './api';

export interface PairingCode {
  code: string;
  expiresIn: number;
  /** This PC's HTTPS address on the tailnet, "" without Tailscale. */
  address: string;
}

export async function newPairingCode(): Promise<PairingCode> {
  const res = await apiFetch('/v1/pairing/code', { method: 'POST' });
  if (!res.ok) throw new Error(`Could not make a code (${res.status})`);
  const body = await res.json();
  return { code: String(body.code), expiresIn: Number(body.expires_in) || 300, address: String(body.address || '') };
}

export async function cancelPairingCode(): Promise<void> {
  await apiFetch('/v1/pairing/code', { method: 'DELETE' }).catch(() => {});
}

export type RedeemResult = { ok: true; apiKey: string } | { ok: false; message: string };

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
    return { ok: true, apiKey: String(body.api_key || '') };
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

/** Whether this device's key is refused: the API answers 401. */
export async function keyRefused(): Promise<boolean> {
  try {
    const res = await apiFetch('/v1/models');
    return res.status === 401;
  } catch {
    return false;
  }
}
