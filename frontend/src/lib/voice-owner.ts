/**
 * Which open Sage page may listen: the one the user used last.
 *
 * Every page runs its own wake word and its own Flux session. With two open
 * -- a second tab, or an old one left behind -- both heard the microphone,
 * both fired on "Hey Sage" and both answered: on 23 September two turn
 * counters ran side by side for three minutes, every turn was handled
 * twice, and the conversation jammed until one page went away.
 *
 * The voice is a Web Lock. A page takes it, stealing it from whoever has
 * it, when it opens and whenever the user focuses or touches it; the page
 * it was taken from stands down and queues for it again. The browser
 * releases a lock however its page ends -- closed, crashed, navigated
 * away -- and hands it to the next page waiting. A first version passed the
 * voice on with a message from the closing page, which the browser could
 * tear down before the message went out: nobody was left listening. A page
 * in the background keeps the voice until another claims it, so Sage still
 * listens while the user works in other apps.
 */

type Listener = (owner: boolean) => void;

const LOCK = 'sage-voice-listener';

let owner = true;
let started = false;
/** Bumped by every request and by stopping: only the latest request's
 * outcome counts. React starts an effect, stops it and starts it again in
 * development; the second start's steal rejected the first start's request,
 * which took this page for having lost the voice to another -- the page
 * stood down while its own request still held the lock. */
let generation = 0;
/** Ends the hold on the lock, when this page has it. */
let releaseHeld: (() => void) | null = null;
/** Cancels this page's place in the queue, when it is waiting. */
let leaveQueue: AbortController | null = null;
const listeners = new Set<Listener>();

function setOwner(next: boolean): void {
  if (owner === next) return;
  owner = next;
  for (const listener of listeners) listener(owner);
}

function locks(): LockManager | null {
  return typeof navigator !== 'undefined' && navigator.locks ? navigator.locks : null;
}

function hold(steal: boolean): void {
  const manager = locks();
  if (!manager) return;
  leaveQueue?.abort();
  leaveQueue = null;
  const mine = ++generation;
  const queue = steal ? null : new AbortController();
  if (queue) leaveQueue = queue;
  manager
    .request(LOCK, steal ? { steal: true } : { signal: queue!.signal }, () => {
      if (mine !== generation) return Promise.resolve();
      if (queue && leaveQueue === queue) leaveQueue = null;
      setOwner(true);
      return new Promise<void>((resolve) => {
        releaseHeld = resolve;
      });
    })
    .catch(() => {
      // Taken by another page: stand down, and wait for it to come back.
      if (!started || mine !== generation) return;
      releaseHeld = null;
      setOwner(false);
      hold(false);
    });
}

/** Take the voice for this page. */
export function claimVoice(): void {
  if (!started || releaseHeld) return;
  hold(true);
}

/** Whether this page may listen. */
export function isVoiceOwner(): boolean {
  return owner;
}

export function onVoiceOwnerChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Start taking part. Returns a function that stops (and lets go). */
export function startVoiceOwnership(): () => void {
  if (!locks()) return () => undefined;
  started = true;
  const claimIfFocused = () => {
    if (document.visibilityState === 'visible' && document.hasFocus()) claimVoice();
  };
  window.addEventListener('focus', claimVoice);
  window.addEventListener('pointerdown', claimVoice);
  window.addEventListener('keydown', claimVoice);
  document.addEventListener('visibilitychange', claimIfFocused);
  hold(true);
  return () => {
    started = false;
    generation += 1;
    window.removeEventListener('focus', claimVoice);
    window.removeEventListener('pointerdown', claimVoice);
    window.removeEventListener('keydown', claimVoice);
    document.removeEventListener('visibilitychange', claimIfFocused);
    leaveQueue?.abort();
    leaveQueue = null;
    releaseHeld?.();
    releaseHeld = null;
  };
}
