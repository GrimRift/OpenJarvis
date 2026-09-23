/**
 * Moments the orb acknowledges without changing state.
 *
 * Kept as a module-level count rather than store state, like the speech
 * level: nothing but the canvas reads it, and a store update would re-render
 * the page for a one-off ripple.
 */

let wakes = 0;

/** The wake word has just fired. */
export function markWake(): void {
  wakes += 1;
}

/** How many times it has fired; the orb ripples when this moves. */
export function wakeCount(): number {
  return wakes;
}
