/**
 * The talk button's two ways in (M29, the user's choice): a tap talks
 * hands-free -- Sage decides when the user has finished, as on the PC -- and
 * a hold is a walkie-talkie: release sends, sliding up cancels.
 *
 * Pure, so the gesture can be tested without a touch screen.
 */

/** Held this long, a press is a hold rather than a tap. */
export const HOLD_AFTER_MS = 350;

/** A finger this far above where it went down is cancelling. */
export const CANCEL_SLIDE_PX = 70;

/** After release, how long a turn Deepgram is still finishing gets to land
 * before what was heard is sent without it. */
export const RELEASE_GRACE_MS = 1500;

export type PressEnd = 'tap' | 'send' | 'cancel';

/** How a press that went down `heldMs` ago, now `slidUpPx` above where it
 * started, ends when the finger lifts. */
export function pressEnd(heldMs: number, slidUpPx: number): PressEnd {
  if (heldMs < HOLD_AFTER_MS) return 'tap';
  return slidUpPx >= CANCEL_SLIDE_PX ? 'cancel' : 'send';
}

/** Whether a press still down is cancelling (to show "release to cancel"). */
export function slidingToCancel(slidUpPx: number): boolean {
  return slidUpPx >= CANCEL_SLIDE_PX;
}

/**
 * What a hold has heard: Deepgram ends a turn at every pause, but while the
 * button is down a pause is not the end, so each finished piece is kept and
 * the whole is sent on release.
 */
export function joinHeld(parts: readonly string[], last = ''): string {
  return [...parts, last]
    .map((part) => part.trim())
    .filter(Boolean)
    .join(' ');
}
