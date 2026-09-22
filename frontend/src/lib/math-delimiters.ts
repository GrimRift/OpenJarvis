/**
 * Turn the LaTeX delimiters the model writes into the ones remark-math reads.
 *
 * remark-math only recognises `$…$` and `$$…$$`. The model writes `\[ … \]`
 * and `\( … \)` (and, after a markdown round trip has eaten the backslash,
 * bare `[ \sum F_x = 0 ]`), which markdown renders as literal brackets — the
 * raw `\sum`, `\frac`, `^\circ` the user could not read. Nothing inside a
 * code span or fence is touched.
 */

const CODE = /(```[\s\S]*?```|`[^`\n]*`)/g;
// A bracket group that contains a TeX command is a formula, not prose.
const TEX_COMMAND = /\\[a-zA-Z]+|\^\{|_\{|\\[{}]/;

function convert(text: string): string {
  let out = text.replace(/\\\[([\s\S]+?)\\\]/g, (_m, body: string) => `$$${body.trim()}$$`);
  out = out.replace(/\\\(([\s\S]+?)\\\)/g, (_m, body: string) => `$${body.trim()}$`);
  // Backslash already eaten: `[ \sum F_x=0 ]` on its own, or `( 0^\circ )`.
  out = out.replace(/\[\s([^\[\]\n]+?)\s\]/g, (m, body: string) =>
    TEX_COMMAND.test(body) ? `$$${body.trim()}$$` : m,
  );
  out = out.replace(/\(([^()\n]*?(?:\\[a-zA-Z]+|\^\\circ)[^()\n]*?)\)/g, (m, body: string) =>
    TEX_COMMAND.test(body) ? `$${body.trim()}$` : m,
  );
  return out;
}

export function normaliseMathDelimiters(text: string): string {
  if (!/[\\[(]/.test(text)) return text;
  return text
    .split(CODE)
    .map((part, i) => (i % 2 === 1 ? part : convert(part)))
    .join('');
}
