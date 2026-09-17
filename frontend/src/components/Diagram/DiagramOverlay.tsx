/**
 * The diagram, over the whole app, while Sage explains it.
 *
 * The app behind is dimmed and blurred and the diagram floats on it with no
 * card of its own -- a white panel over a dark chat reads as a different
 * program. It closes on Esc, on the button, on a click outside, and by itself
 * when the voice that opened it stops; a diagram nobody spoke waits for the
 * user (their choice, so a typed answer is never snatched away mid-read).
 */

import { useEffect, useMemo } from 'react';
import type { Diagram } from '../../lib/diagram';
import { activeNodeIndex } from '../../lib/diagram';
import { Arrow, DiagramBox, Legend, RESULT, tint } from './DiagramParts';

const ACCENT = '#22d3ee';
/** Wide enough for three boxes and their arrows before wrapping. */
const ROW = 3;

interface Props {
  diagram: Diagram;
  /** Everything Sage has said aloud so far, for the confident highlight. */
  spoken: string;
  /** True while the voice that opened this is still going. */
  speaking: boolean;
  /** Whether this diagram was ever spoken at all. */
  wasSpoken: boolean;
  onClose: () => void;
}

export function DiagramOverlay({ diagram, spoken, speaking, wasSpoken, onClose }: Props) {
  const active = useMemo(
    () => (speaking ? activeNodeIndex(spoken, diagram.nodes) : -1),
    [speaking, spoken, diagram.nodes],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Spoken diagrams leave when the voice does; typed ones wait to be closed.
  useEffect(() => {
    if (wasSpoken && !speaking) {
      const timer = setTimeout(onClose, 900);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [wasSpoken, speaking, onClose]);

  // Indices are carried, never recovered with indexOf: a comparison names the
  // same row on both sides ("Light gathering" against a lens and a mirror),
  // so a label identifies nothing.
  const placed = diagram.nodes.map((node, index) => ({ node, index }));
  const rows: (typeof placed)[] = [];
  if (diagram.shape === 'flow') {
    for (let i = 0; i < placed.length; i += ROW) {
      rows.push(placed.slice(i, i + ROW));
    }
  }

  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 80 }}
      onClick={onClose}
      role="presentation"
    >
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(10, 10, 11, 0.84)', backdropFilter: 'blur(3px)' }} />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage: 'radial-gradient(rgba(255,255,255,0.06) 1px, transparent 1px)',
          backgroundSize: '24px 24px',
          WebkitMaskImage: 'radial-gradient(ellipse 58% 52% at 50% 48%, #000 38%, transparent 76%)',
          maskImage: 'radial-gradient(ellipse 58% 52% at 50% 48%, #000 38%, transparent 76%)',
        }}
      />

      <div
        style={{
          position: 'absolute',
          inset: 0,
          boxSizing: 'border-box',
          padding: '64px 48px 44px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 22,
          overflowY: 'auto',
          color: '#fafafa',
        }}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={diagram.title}
      >
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 7 }}>
          {speaking ? (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 9,
                padding: '5px 13px 5px 9px',
                borderRadius: 999,
                background: tint(ACCENT, 0.12),
                border: `1px solid ${tint(ACCENT, 0.38)}`,
              }}
            >
              <span style={{ width: 7, height: 7, borderRadius: 999, background: ACCENT }} />
              <span style={{ fontSize: 11, letterSpacing: '0.09em', textTransform: 'uppercase', color: ACCENT, fontWeight: 700 }}>
                Sage is explaining
              </span>
            </div>
          ) : null}
          <div style={{ fontSize: 27, fontWeight: 600, letterSpacing: '-0.02em', textAlign: 'center' }}>
            {diagram.title}
          </div>
        </div>

        {diagram.shape === 'flow'
          ? rows.map((row, rowIndex) => {
              const backwards = rowIndex % 2 === 1;
              const cells = backwards ? [...row].reverse() : row;
              return (
                <div key={rowIndex} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 }}>
                  {rowIndex > 0 ? <Arrow direction="down" colour="#3f3f46" /> : null}
                  <div style={{ display: 'flex', alignItems: 'stretch', gap: 12, flexWrap: 'wrap', justifyContent: 'center' }}>
                    {cells.map(({ node, index }, i) => (
                      <div key={index} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        {i > 0 ? <Arrow direction={backwards ? 'left' : 'right'} colour="#3f3f46" /> : null}
                        <DiagramBox node={node} index={index} active={index === active} accent={ACCENT} />
                      </div>
                    ))}
                  </div>
                </div>
              );
            })
          : null}

        {diagram.shape === 'parts' ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 18 }}>
            <div
              style={{
                padding: '12px 26px',
                borderRadius: 14,
                background: tint(RESULT, 0.12),
                border: `1px solid ${tint(RESULT, 0.3)}`,
                fontSize: 18,
                fontWeight: 600,
              }}
            >
              {diagram.subject}
            </div>
            <Arrow direction="down" colour="#3f3f46" />
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', justifyContent: 'center', maxWidth: 960 }}>
              {placed.map(({ node, index }) => (
                <DiagramBox key={index} node={node} active={index === active} accent={ACCENT} width={228} />
              ))}
            </div>
          </div>
        ) : null}

        {diagram.shape === 'comparison' && diagram.sides ? (
          <div style={{ display: 'flex', gap: 34, alignItems: 'flex-start', flexWrap: 'wrap', justifyContent: 'center' }}>
            {[0, 1].map((side) => (
              <div key={side} style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center' }}>
                <div style={{ fontSize: 15, fontWeight: 600, color: '#e4e4e7', paddingBottom: 2 }}>
                  {diagram.sides?.[side]}
                </div>
                {placed
                  .filter(({ node }) => (node.side ?? 0) === side)
                  .map(({ node, index }) => (
                    <DiagramBox
                      key={index}
                      node={node}
                      active={index === active}
                      accent={ACCENT}
                      width={286}
                    />
                  ))}
              </div>
            ))}
          </div>
        ) : null}

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 11, marginTop: 2 }}>
          {speaking ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '11px 18px', borderRadius: 999, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.10)' }}>
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke={ACCENT} strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M3 12h2" /><path d="M7 8v8" /><path d="M11 5v14" /><path d="M15 9v6" /><path d="M19 11v2" />
              </svg>
              <span style={{ fontSize: 13.5, color: '#e4e4e7' }}>
                {active >= 0
                  ? `Speaking step ${active + 1} of ${diagram.nodes.length} — ${diagram.nodes[active].label.toLowerCase()}.`
                  : 'Sage is explaining this.'}
              </span>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderRadius: 999, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.10)' }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#a1a1aa" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <circle cx="12" cy="12" r="9" /><path d="M12 8v4" /><path d="M12 16h.01" />
              </svg>
              <span style={{ fontSize: 13.5, color: '#e4e4e7' }}>This stays open until you close it.</span>
            </div>
          )}
          <Legend accent={ACCENT} showActive={speaking} />
          <div style={{ fontSize: 11.5, color: '#71717a' }}>Esc or Close to dismiss · reopen from the message</div>
        </div>
      </div>

      <button
        type="button"
        aria-label="Close diagram"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
        style={{
          position: 'absolute',
          top: 22,
          right: 22,
          width: 44,
          height: 44,
          borderRadius: 999,
          background: 'rgba(255,255,255,0.06)',
          border: '1px solid rgba(255,255,255,0.12)',
          color: '#e4e4e7',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: 'pointer',
        }}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d="M18 6 6 18" /><path d="m6 6 12 12" />
        </svg>
      </button>
    </div>
  );
}
