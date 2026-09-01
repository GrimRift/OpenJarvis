/**
 * Attaching images to a chat turn.
 *
 * Pure logic, kept out of the component so it can be tested — this project has
 * no jsdom, so anything living inside `InputArea` is untestable by definition.
 *
 * Images are ephemeral: they ride one request, are never indexed, and are
 * stripped before conversations are written to localStorage. A screenshot is
 * 1–3MB of base64, and storing a few would quietly fill the quota.
 */

/** Beyond this, a mistake gets expensive: each image costs tokens. */
export const MAX_IMAGES = 4;

/** Per-image ceiling before base64 inflates it by a third. */
export const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

export const ACCEPTED_IMAGE_TYPES = [
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
] as const;

export interface AttachedImage {
  id: string;
  /** Full `data:` URL — what both the thumbnail and the request need. */
  dataUrl: string;
  name: string;
  bytes: number;
}

export type AttachResult =
  | { ok: true; images: AttachedImage[] }
  | { ok: false; reason: string; images: AttachedImage[] };

export function isAcceptedImage(type: string): boolean {
  return (ACCEPTED_IMAGE_TYPES as readonly string[]).includes(type);
}

function describeBytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

/**
 * Decide what to keep when new files arrive.
 *
 * Returns the images to hold *and*, when something was refused, a reason worth
 * showing. Silently dropping a file the user just dragged in reads as the app
 * being broken.
 */
export function planAttachments(
  existing: AttachedImage[],
  incoming: Array<{ name: string; type: string; size: number }>,
): { accepted: number[]; rejected: string | null } {
  const accepted: number[] = [];
  const problems: string[] = [];
  let room = MAX_IMAGES - existing.length;

  incoming.forEach((file, index) => {
    if (!isAcceptedImage(file.type)) {
      problems.push(`${file.name} is not an image`);
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      problems.push(
        `${file.name} is ${describeBytes(file.size)}, over the ` +
          `${describeBytes(MAX_IMAGE_BYTES)} limit`,
      );
      return;
    }
    if (room <= 0) {
      problems.push(`only ${MAX_IMAGES} images per message`);
      return;
    }
    room -= 1;
    accepted.push(index);
  });

  // One reason, not a wall: repeats collapse when several files fail the same
  // way, which is the common case for a multi-select.
  const unique = Array.from(new Set(problems));
  return { accepted, rejected: unique.length ? unique.join('; ') : null };
}

/** Extract image files from a paste or drop, ignoring everything else. */
export function imageFilesFrom(
  items: Array<{ kind?: string; type: string; getAsFile?: () => File | null }>,
): File[] {
  const files: File[] = [];
  for (const item of items) {
    if (item.kind && item.kind !== 'file') continue;
    if (!isAcceptedImage(item.type)) continue;
    const file = item.getAsFile?.();
    if (file) files.push(file);
  }
  return files;
}

/**
 * Strip images from stored conversations.
 *
 * Called on the way to localStorage. Session-only is a deliberate choice: the
 * thumbnail is there while the tab is open, and the quota is not spent on
 * screenshots.
 */
export function withoutImages<T extends { messages: Array<{ images?: unknown }> }>(
  conversation: T,
): T {
  if (!conversation.messages?.length) return conversation;
  return {
    ...conversation,
    messages: conversation.messages.map((message) => {
      if (!message.images) return message;
      const { images: _dropped, ...rest } = message;
      return rest as typeof message;
    }),
  };
}
