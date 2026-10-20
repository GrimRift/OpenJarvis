/**
 * The phone and tablet layout (M29): bottom tabs, a chats drawer, a bigger
 * talk button, the Voice orb sized to the screen.
 *
 * It is chosen by the device, not the window width: a narrow desktop window
 * keeps the desktop layout, and an iPad -- wider than Tailwind's md
 * breakpoint -- gets the touch one, scaled up (`touchZoomFor`). The
 * desktop app never gets it.
 */
import { useEffect, useState } from 'react';

export interface ScreenFacts {
  /** `(pointer: coarse)`: the main pointer is a finger. */
  coarse: boolean;
  width: number;
  height: number;
  /** Running inside the Tauri desktop app. */
  desktopShell: boolean;
}

/** The longest side of the biggest iPad (13", 1366 pt). Anything larger with
 * a touch screen is a touch laptop or a TV: those keep the desktop layout. */
export const TOUCH_MAX_SIDE = 1366;

/** A screen whose short side is at least this is a tablet. */
export const TABLET_MIN_SIDE = 700;

/** A phone's width the layout is drawn for; a tablet is scaled up from it. */
const PHONE_DESIGN_WIDTH = 430;

/** How far a tablet is scaled: the user asked for the phone layout, bigger. */
const TABLET_MAX_ZOOM = 1.3;

export function isTouchLayout(facts: ScreenFacts): boolean {
  if (facts.desktopShell || !facts.coarse) return false;
  return Math.max(facts.width, facts.height) <= TOUCH_MAX_SIDE;
}

/**
 * The CSS zoom for the touch layout: 1 on a phone, up to 1.3 on a tablet so
 * an iPad shows the phone layout larger rather than a stretched one.
 */
export function touchZoomFor(width: number, height: number): number {
  const short = Math.min(width, height);
  if (!(short >= TABLET_MIN_SIDE)) return 1;
  const zoom = short / (PHONE_DESIGN_WIDTH * 1.4);
  return Math.round(Math.min(TABLET_MAX_ZOOM, Math.max(1, zoom)) * 100) / 100;
}

/** "Layout: touch" forced from the address bar (`?layout=touch|desktop`),
 * remembered for the tab, so the layout can be checked on any screen. */
function forcedLayout(): 'touch' | 'desktop' | null {
  try {
    const asked = new URLSearchParams(window.location.search).get('layout');
    if (asked === 'touch' || asked === 'desktop') {
      sessionStorage.setItem('sage-layout', asked);
      return asked;
    }
    const kept = sessionStorage.getItem('sage-layout');
    return kept === 'touch' || kept === 'desktop' ? kept : null;
  } catch {
    return null;
  }
}

export function readScreenFacts(): ScreenFacts {
  return {
    coarse: window.matchMedia?.('(pointer: coarse)').matches ?? false,
    width: window.screen?.width || window.innerWidth,
    height: window.screen?.height || window.innerHeight,
    desktopShell: !!(window as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__,
  };
}

export function touchLayoutNow(): boolean {
  if (typeof window === 'undefined') return false;
  const forced = forcedLayout();
  if (forced) return forced === 'touch';
  return isTouchLayout(readScreenFacts());
}

/** Whether this device uses the touch layout. Fixed for the page's life:
 * a device does not stop being a phone, and flipping layouts would remount
 * the message box, which owns the microphone. */
export function useTouchLayout(): boolean {
  const [touch] = useState(touchLayoutNow);
  return touch;
}

/**
 * Marks the page as touch (`html[data-touch]`, which index.css keys on) and
 * scales a tablet up. The scaling is the viewport's width, not CSS zoom: the
 * browser then scales everything itself -- canvas, touch positions, media
 * queries -- which CSS zoom does not do the same way in Safari and Chrome.
 */
export function applyTouchLayout(): void {
  document.documentElement.setAttribute('data-touch', '');
  const meta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]');
  if (!meta) return;
  const fit = () => {
    const width = window.screen?.width || window.innerWidth;
    const height = window.screen?.height || window.innerHeight;
    // screen.width is the portrait width on iOS whichever way it is held.
    const landscape = window.matchMedia?.('(orientation: landscape)').matches ?? false;
    const across = landscape ? Math.max(width, height) : Math.min(width, height);
    const zoom = touchZoomFor(width, height);
    meta.content =
      zoom > 1
        ? `width=${Math.round(across / zoom)}, viewport-fit=cover`
        : 'width=device-width, initial-scale=1.0, viewport-fit=cover';
  };
  fit();
  window.addEventListener('orientationchange', fit);
  window.matchMedia?.('(orientation: landscape)').addEventListener?.('change', fit);
  followVisibleArea();
}

/**
 * Keeps the app inside the part of the screen the keyboard leaves. iOS does
 * not shrink the page for its keyboard: it scrolls the whole page up to show
 * the focused box, which carried the header off the top and left the message
 * box out of sight (M29, first iPhone test). The touch shell is pinned to the
 * visual viewport instead, through --vv-top and --vv-height (index.css).
 */
function followVisibleArea(): void {
  const vv = window.visualViewport;
  if (!vv) return;
  const root = document.documentElement;
  const update = () => {
    root.style.setProperty('--vv-top', `${Math.round(vv.offsetTop)}px`);
    root.style.setProperty('--vv-height', `${Math.round(vv.height)}px`);
  };
  update();
  vv.addEventListener('resize', update);
  vv.addEventListener('scroll', update);
}

/** The Voice orb on a phone or iPad: as big as the screen allows under the
 * status and over the controls, never the desktop's 764. */
export function touchOrbSize(width: number, height: number): number {
  return Math.round(Math.max(180, Math.min(width - 56, height - 330, 360)));
}

/** The on-screen keyboard shrinks the visual viewport by more than this. */
const KEYBOARD_MIN_PX = 150;

/** Whether the on-screen keyboard is up (the tab bar steps aside for it). */
export function keyboardOpen(layoutHeight: number, visualHeight: number): boolean {
  return layoutHeight - visualHeight > KEYBOARD_MIN_PX;
}

export function useKeyboardOpen(): boolean {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    // The layout viewport's height, not window.innerHeight: on iOS
    // innerHeight follows the keyboard too, so the two never differed and
    // the tab bar stayed up over the keys.
    const check = () => setOpen(keyboardOpen(document.documentElement.clientHeight, vv.height));
    vv.addEventListener('resize', check);
    window.addEventListener('focusin', check);
    window.addEventListener('focusout', check);
    return () => {
      vv.removeEventListener('resize', check);
      window.removeEventListener('focusin', check);
      window.removeEventListener('focusout', check);
    };
  }, []);
  return open;
}

/** Whether the page is on screen: false once Safari is in the background or
 * the phone is locked, when nothing should keep listening (M29). */
export function usePageVisible(): boolean {
  const read = () => typeof document === 'undefined' || document.visibilityState !== 'hidden';
  const [visible, setVisible] = useState(read);
  useEffect(() => {
    const onChange = () => setVisible(read());
    document.addEventListener('visibilitychange', onChange);
    return () => document.removeEventListener('visibilitychange', onChange);
  }, []);
  return visible;
}

/** On a phone the microphone opens only when it is needed (M29): for a turn
 * the user asked for, or for "Hey Sage" where this phone's setting lets it
 * listen -- never while the page is out of sight. */
export function touchMicOpen(state: {
  visible: boolean;
  turnWanted: boolean;
  wakeWord: 'off' | 'voice' | 'always';
  wakeWordOn: boolean;
  onVoiceTab: boolean;
}): { mic: boolean; wake: boolean } {
  if (!state.visible) return { mic: false, wake: false };
  const wake =
    state.wakeWordOn &&
    (state.wakeWord === 'always' || (state.wakeWord === 'voice' && state.onVoiceTab));
  return { mic: state.turnWanted || wake, wake };
}
