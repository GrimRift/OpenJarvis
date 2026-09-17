/**
 * The pieces both the overlay and the in-chat card draw with.
 *
 * Colour is deliberately scarce: neutral is the default, the accent marks the
 * step being spoken, amber marks what sets a process off and purple where the
 * new thing forms. Three meanings, never decoration.
 */

import type { DiagramMark, DiagramNode } from '../../lib/diagram';

export const TRIGGER = '#f59e0b';
export const RESULT = '#8b5cf6';

export function markColour(mark?: DiagramMark): string | null {
  if (mark === 'trigger') return TRIGGER;
  if (mark === 'result') return RESULT;
  return null;
}

/** rgba from a #rrggbb, so a tint can be mixed without a colour library. */
export function tint(hex: string, alpha: number): string {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!match) return `rgba(34, 211, 238, ${alpha})`;
  const n = parseInt(match[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

const PATHS: Record<string, string[]> = {
  crack: ['M13 3 8 11h4l-3 10'],
  droplet: ['M12 3s5.5 6.2 5.5 9.8a5.5 5.5 0 0 1-11 0C6.5 9.2 12 3 12 3Z'],
  spark: ['M12 3.5v3', 'M12 17.5v3', 'M3.5 12h3', 'M17.5 12h3', 'm6.3 6.3 2.1 2.1', 'm15.6 15.6 2.1 2.1'],
  crystal: ['m12 3 7 6-7 12-7-12 7-6Z'],
  rod: ['M6.5 14.5a4 4 0 0 1 0-5l3-3a4 4 0 0 1 5.6 5.6l-3 3a4 4 0 0 1-5.6 0Z'],
  close: ['M4 13h5', 'm7 10 3 3-3 3', 'M20 13h-5', 'm17 16-3-3 3-3'],
  flame: ['M12 3c3 4 5 6 5 9a5 5 0 0 1-10 0c0-1.5.7-2.8 1.8-4C9.5 9.5 11 7 12 3Z'],
  layers: ['m12 3 8 4-8 4-8-4 8-4Z', 'm4 13 8 4 8-4'],
  tool: ['M15 3a5 5 0 0 0-4.6 7L3 17.4V21h3.6l7.4-7.4A5 5 0 0 0 15 3Z'],
  gear: ['M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z', 'M12 2v3', 'M12 19v3', 'M2 12h3', 'M19 12h3'],
  clock: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z', 'M12 7v5l3 2'],
  check: ['m5 13 4 4L19 7'],
  bolt: ['M13 2 4 14h6l-1 8 9-12h-6l1-8Z'],
  beaker: ['M9 3v6L4 19a2 2 0 0 0 1.8 3h12.4A2 2 0 0 0 20 19l-5-10V3', 'M8 3h8'],
  scale: ['M12 4v16', 'M5 8h14', 'm5 8-3 6h6l-3-6Z', 'm19 8-3 6h6l-3-6Z'],
  eye: ['M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z'],
  cpu: ['M7 7h10v10H7z', 'M4 10h3', 'M4 14h3', 'M17 10h3', 'M17 14h3', 'M10 4v3', 'M14 4v3', 'M10 17v3', 'M14 17v3'],
  box: ['M4 8l8-4 8 4v8l-8 4-8-4V8Z', 'm4 8 8 4 8-4', 'M12 12v8'],
  leaf: ['M4 20c0-8 6-14 16-14 0 10-6 15-16 14Z', 'M9 15c2-2 4-3 7-4'],
  wave: ['M3 12c2-4 4-4 6 0s4 4 6 0 4-4 6 0'],
};

export function DiagramIcon({ name, colour }: { name?: string; colour: string }) {
  const paths = (name && PATHS[name]) || null;
  if (!paths) {
    return (
      <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="3.2" fill={colour} />
      </svg>
    );
  }
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke={colour}
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

export function Arrow({ direction, colour }: { direction: 'right' | 'left' | 'down'; colour: string }) {
  if (direction === 'down') {
    return (
      <svg width="20" height="28" viewBox="0 0 20 28" fill="none" stroke={colour} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M10 2v20" />
        <path d="m5 17 5 5 5-5" />
      </svg>
    );
  }
  const flip = direction === 'left';
  return (
    <svg width="30" height="20" viewBox="0 0 30 20" fill="none" stroke={colour} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={flip ? 'M28 10H6' : 'M2 10h22'} />
      <path d={flip ? 'm11 5-5 5 5 5' : 'm19 5 5 5-5 5'} />
    </svg>
  );
}

interface NodeProps {
  node: DiagramNode;
  index?: number;
  active?: boolean;
  accent: string;
  width?: number;
}

export function DiagramBox({ node, index, active, accent, width = 300 }: NodeProps) {
  const mark = markColour(node.mark);
  const iconColour = active ? '#06232b' : (mark ?? '#a1a1aa');
  const tileBg = active ? accent : mark ? tint(mark, 0.14) : 'rgba(255,255,255,0.06)';

  return (
    <div
      style={{
        width,
        boxSizing: 'border-box',
        padding: '15px 16px',
        borderRadius: 15,
        background: active ? tint(accent, 0.12) : 'rgba(255,255,255,0.035)',
        border: `1px solid ${active ? accent : 'rgba(255,255,255,0.10)'}`,
        boxShadow: active ? `0 12px 38px ${tint(accent, 0.22)}` : 'none',
        display: 'flex',
        gap: 12,
        transition: 'background 220ms ease, border-color 220ms ease, box-shadow 220ms ease',
      }}
    >
      <div
        style={{
          width: 32,
          height: 32,
          borderRadius: 10,
          background: tileBg,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          transition: 'background 220ms ease',
        }}
      >
        <DiagramIcon name={node.icon} colour={iconColour} />
      </div>
      <div style={{ flexGrow: 1, display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 15, fontWeight: 600, letterSpacing: '-0.01em' }}>{node.label}</span>
          {active ? (
            <span style={{ marginLeft: 'auto', width: 7, height: 7, borderRadius: 999, background: accent, flexShrink: 0 }} />
          ) : index !== undefined ? (
            <span style={{ marginLeft: 'auto', fontSize: 11, color: '#52525b', flexShrink: 0 }}>{index + 1}</span>
          ) : null}
        </div>
        {node.note ? (
          <div style={{ fontSize: 12.5, lineHeight: 1.45, color: active ? '#d4d4d8' : '#a1a1aa' }}>{node.note}</div>
        ) : null}
      </div>
    </div>
  );
}

export function Legend({ accent, showActive }: { accent: string; showActive: boolean }) {
  const items: Array<[string, string]> = [];
  if (showActive) items.push([accent, 'being said now']);
  items.push([TRIGGER, 'what sets it off']);
  items.push([RESULT, 'where it forms']);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap', justifyContent: 'center' }}>
      {items.map(([colour, label]) => (
        <span key={label} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: '#71717a' }}>
          <span style={{ width: 7, height: 7, borderRadius: 999, background: colour }} />
          {label}
        </span>
      ))}
    </div>
  );
}
