/**
 * Short table cells stay on one line.
 *
 * The browser shares a table's width out by content, so a narrow label column
 * was squeezed until "Problem(s)" broke as "Problem(" / "s)" and a problem
 * list "7, 12, 31, 36" spilled onto a second line. A short cell is a label or
 * a number list, never prose: keeping it whole costs a few pixels and the
 * long columns still wrap.
 */
export const ONE_LINE_MAX_CHARS = 20;

interface HastLike {
  type?: string;
  value?: string;
  children?: HastLike[];
}

/** The plain text of a hast node, as the cell shows it. */
export function hastText(node: HastLike | undefined): string {
  if (!node) return '';
  if (node.type === 'text') return node.value ?? '';
  return (node.children ?? []).map(hastText).join('');
}

export function keepOnOneLine(text: string): boolean {
  const trimmed = text.trim();
  return trimmed.length > 0 && trimmed.length <= ONE_LINE_MAX_CHARS;
}
