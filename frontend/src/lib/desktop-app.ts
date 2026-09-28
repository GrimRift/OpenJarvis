// The Sage Windows app's startup options (M39): start with Windows, stay in
// the tray when started that way, and a keyboard shortcut that shows or hides
// the window from anywhere. They belong to the app, not the server, so they
// are read and changed through the app itself; in a browser tab there is no
// such thing and Settings leaves the section out (see inDesktopShell).
import { invoke } from '@tauri-apps/api/core';

export interface StartupSettings {
  startWithWindows: boolean;
  startHidden: boolean;
  /** e.g. "Ctrl+Alt+KeyS"; null means no shortcut. */
  shortcut: string | null;
}

export const getStartupSettings = () => invoke<StartupSettings>('get_startup_settings');

/** Applies and saves; resolves to what is now in effect, rejects with a readable reason. */
export const setStartupSettings = (settings: StartupSettings) =>
  invoke<StartupSettings>('set_startup_settings', { settings });

const MODIFIER_CODES = /^(Control|Alt|Shift|Meta|OS)(Left|Right)?$/;

type KeyLike = Pick<KeyboardEvent, 'code' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey'>;

/**
 * The shortcut a key press spells, in the form the app registers
 * ("Ctrl+Alt+KeyS"), or null while only modifiers are down. Ctrl, Alt or Win
 * is required: Shift+letter alone would steal ordinary typing everywhere.
 */
export function shortcutFromKey(event: KeyLike): string | null {
  if (!event.code || MODIFIER_CODES.test(event.code)) return null;
  if (!event.ctrlKey && !event.altKey && !event.metaKey) return null;
  const parts: string[] = [];
  if (event.ctrlKey) parts.push('Ctrl');
  if (event.altKey) parts.push('Alt');
  if (event.shiftKey) parts.push('Shift');
  if (event.metaKey) parts.push('Super');
  parts.push(event.code);
  return parts.join('+');
}

/** "Ctrl+Alt+KeyS" -> "Ctrl + Alt + S", for showing to the user. */
export function formatShortcut(shortcut: string): string {
  return shortcut
    .split('+')
    .map((part) => part.replace(/^Key(?=[A-Z]$)/, '').replace(/^Digit(?=\d$)/, '').replace(/^Super$/, 'Win'))
    .join(' + ');
}
