/**
 * The audiobook position slider (Books page and reader). A click jumps there
 * (user's choice, 2026-10-13). The thumb is centred on the bar, so in a row
 * with `items-center` the bar lines up with the text beside it (2026-10-14).
 */
export function Scrubber({
  pct,
  disabled,
  onSeek,
  className = 'mt-4 mb-1',
}: {
  pct: number;
  disabled: boolean;
  onSeek: (fraction: number) => void;
  className?: string;
}) {
  return (
    <div
      className={`relative h-1.5 rounded-full ${className}`}
      style={{ background: 'var(--color-bg-tertiary)', cursor: disabled ? 'default' : 'pointer' }}
      onClick={(e) => {
        if (disabled) return;
        const r = e.currentTarget.getBoundingClientRect();
        onSeek(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)));
      }}
    >
      <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${pct}%`, background: 'var(--color-accent)' }} />
      <div
        className="absolute w-3.5 h-3.5 rounded-full top-1/2 -translate-y-1/2"
        style={{ left: `calc(${pct}% - 7px)`, background: '#fff', opacity: disabled ? 0.5 : 1 }}
      />
    </div>
  );
}
