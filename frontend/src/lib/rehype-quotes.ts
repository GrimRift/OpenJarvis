interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

// Quoted text inside these is left alone: code is literal, links and bold
// already have their own colour.
const SKIP_TAGS = new Set(['code', 'pre', 'script', 'style', 'a', 'strong', 'b']);

// A "quoted phrase" (straight or curly quotes) on one line, up to 120 chars.
// The opening quote must start the text or follow a space/bracket/dash and
// the closing one must end a word, so inch marks (27" monitor) don't pair.
const QUOTE_RE =
  /(^|[\s([{—–-])(["“])([^"“”\n]{1,120}?)(["”])(?=$|[\s.,;:!?)\]}—–-])/g;

function isKatex(node: HastNode): boolean {
  const cls = node.properties?.className;
  return Array.isArray(cls) && cls.some((c) => String(c).startsWith('katex'));
}

export function splitQuotes(text: string): HastNode[] | null {
  if (!/["“]/.test(text)) return null;
  const pieces: HastNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  QUOTE_RE.lastIndex = 0;
  while ((m = QUOTE_RE.exec(text)) !== null) {
    if (!m[3].trim()) continue;
    const start = m.index + m[1].length;
    const end = m.index + m[0].length;
    if (start > last) pieces.push({ type: 'text', value: text.slice(last, start) });
    pieces.push({
      type: 'element',
      tagName: 'span',
      properties: { className: ['sage-quote'] },
      children: [{ type: 'text', value: text.slice(start, end) }],
    });
    last = end;
  }
  if (pieces.length === 0) return null;
  if (last < text.length) pieces.push({ type: 'text', value: text.slice(last) });
  return pieces;
}

/** Wrap "quoted phrases" in Sage's replies in a span so they can be tinted
 *  like bold text (`.prose .sage-quote`). */
export function rehypeQuotes() {
  function walk(node: HastNode): void {
    if (!node.children) return;
    const out: HastNode[] = [];
    let changed = false;
    for (const child of node.children) {
      if (child.type === 'element') {
        if (!SKIP_TAGS.has(child.tagName ?? '') && !isKatex(child)) walk(child);
        out.push(child);
      } else if (child.type === 'text' && typeof child.value === 'string') {
        const split = splitQuotes(child.value);
        if (split) {
          out.push(...split);
          changed = true;
        } else {
          out.push(child);
        }
      } else {
        out.push(child);
      }
    }
    if (changed) node.children = out;
  }
  return (tree: HastNode) => {
    walk(tree);
  };
}
