/**
 * Stop `remark-math` from reading currency as inline math.
 *
 * A live transcript containing "$200" and, a sentence later, "$1" rendered as
 * `200invoiceAIcreditswereactivated...1` — remark-math paired the two dollar
 * signs and KaTeX then dropped every space between them. For an assistant that
 * talks about invoices and subscriptions this is far more common than inline
 * math, so currency wins the ambiguity.
 *
 * Only a `$` that begins something shaped like money is escaped:
 *   - `$200`, `$1`, `$1,234.56`  -> escaped, rendered literally
 *   - `$x^2$`, `$\alpha$`        -> untouched, still math
 *   - `$$ ... $$` display math   -> untouched
 *   - `$2x + 1$`                 -> untouched (digit followed by a word char)
 *
 * The one real loss is inline math whose opening delimiter is followed by a
 * bare number and a non-word character, such as `$1 + 1$`. Write that as
 * `$$1 + 1$$` if it is ever needed.
 *
 * Code spans and fenced blocks are left alone: escaping inside them would show
 * a literal backslash to the user.
 */

// A money amount: digits, optional thousands groups, optional decimals — not
// followed by a word character, `^` or `_`, which would make it look like math.
const MONEY = /(?<![\\$])\$(?=\d{1,3}(?:,\d{3})*(?:\.\d+)?(?![\w^_])|\d+(?:\.\d+)?(?![\w^_]))/g;

// Fenced blocks (``` or ~~~) and inline code spans, so they can be skipped.
const CODE = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g;

export function protectCurrencyFromMath(text: string): string {
  if (!text.includes('$')) return text;

  return text
    .split(CODE)
    .map((part, i) => (i % 2 === 1 ? part : part.replace(MONEY, '\\$')))
    .join('');
}
