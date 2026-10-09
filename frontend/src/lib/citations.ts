/**
 * The model's own citation markers, made readable.
 *
 * gpt-6-luna sometimes writes OpenAI's private-use citation markup into the
 * text: U+E200 "cite" U+E202 <url or turn id> U+E201. The marks are invisible,
 * so the reply showed "citehttps://www.xda-developers.com/..." glued to the
 * sentence (9 October). A URL becomes a short source link; an internal id
 * ("turn0search0") means nothing to the reader and is dropped. A marker still
 * arriving is hidden until it closes.
 */
const MARKER = /cite([^]*)/g;
const UNFINISHED = /[^]*$/;
const STRAY = /[-]/g;

function sourceLink(target: string): string {
  const urls = target.split(/[\s]+/).filter((part) => /^https?:\/\//.test(part));
  return urls
    .map((url) => {
      let host = url;
      try {
        host = new URL(url).hostname.replace(/^www\./, '');
      } catch {
        // Keep the raw text as the label.
      }
      return ` ([${host}](${url}))`;
    })
    .join('');
}

export function cleanCitations(text: string): string {
  if (!text.includes('') && !STRAY.test(text)) return text;
  STRAY.lastIndex = 0;
  return text
    .replace(MARKER, (_match, target: string) => sourceLink(target))
    .replace(UNFINISHED, '')
    .replace(STRAY, '');
}
