/**
 * Clicking the dimmed backdrop closes an overlay (diagram or image).
 *
 * Both overlays centre their content in a full-screen layer that stops clicks
 * reaching the dim layer beneath it, so a click on the "background" actually
 * lands on that layer -- and the diagram's version swallowed it, so it never
 * closed. The rule: a click whose target is the full-screen layer itself hit
 * empty space and closes; a click on anything inside it (the picture, a box,
 * a button) does not.
 */
export function isBackdropClick(event: { target: unknown; currentTarget: unknown }): boolean {
  return event.target === event.currentTarget;
}
