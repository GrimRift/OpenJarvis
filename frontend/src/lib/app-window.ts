// Whether the Sage Windows app's window is out of sight (hidden in the tray
// or minimised). The app keeps its page "visible" on purpose so the wake word
// and timers never slow down, so the page cannot tell by itself; the app sets
// this flag instead (lib.rs, tell_page). Always false in a browser, where the
// browser already stops animation in a background tab.
declare global {
  interface Window {
    __sageWindowHidden?: boolean;
  }
}

export const appWindowHidden = (): boolean =>
  typeof window !== 'undefined' && window.__sageWindowHidden === true;

/**
 * Run `callback` once, the next time the app says its window is back in
 * sight. Returns a function that cancels the wait. An animation loop that
 * stops asking for frames while hidden lets the graphics process rest too:
 * merely requesting frames kept it at a third of a core (29 September).
 */
export function whenAppWindowShown(callback: () => void): () => void {
  const onChange = () => {
    if (appWindowHidden()) return;
    window.removeEventListener('sage-window-visibility', onChange);
    callback();
  };
  window.addEventListener('sage-window-visibility', onChange);
  return () => window.removeEventListener('sage-window-visibility', onChange);
}

// Mirror the flag onto <html data-app-hidden>, where index.css pauses every
// CSS animation. Set once now (the app may have spoken before this loaded).
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const mirror = () => document.documentElement.toggleAttribute('data-app-hidden', appWindowHidden());
  mirror();
  window.addEventListener('sage-window-visibility', mirror);
}
