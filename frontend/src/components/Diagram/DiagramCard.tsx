/**
 * What a diagram leaves behind in the conversation.
 *
 * The overlay is transient; this is the record. It replaces the text sketch
 * Sage used to print into a code block, and reopens the full drawing.
 */

import { useEffect, useMemo } from 'react';
import { parseDiagram } from '../../lib/diagram';
import { diagramKey, useDiagramPresenter } from '../../lib/diagram-presenter';
import { useAppStore } from '../../lib/store';
import { DiagramIcon, markColour, tint } from './DiagramParts';

const ACCENT = '#0891b2';

interface Props {
  source: string;
  messageId: string;
  /** This message is the one being answered right now. */
  isLive?: boolean;
}

export function DiagramCard({ source, messageId, isLive }: Props) {
  const diagram = useMemo(() => parseDiagram(source), [source]);
  const settings = useAppStore((s) => s.settings);
  const open = useDiagramPresenter((s) => s.open);
  const isNew = useDiagramPresenter((s) => s.isNew);
  const key = diagramKey(messageId, source);

  // Shown full-screen as it arrives -- but only for the answer being given
  // now, and only once, so scrolling back through the history is quiet.
  useEffect(() => {
    if (!diagram || !isLive || !settings.diagramsEnabled) return;
    if (!isNew(key)) return;
    open(key, diagram);
  }, [diagram, isLive, settings.diagramsEnabled, key, isNew, open]);

  if (!diagram || !settings.diagramsEnabled) return null;

  const steps = diagram.nodes.slice(0, 6);

  return (
    <div
      className="my-3"
      style={{
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--color-surface)',
        overflow: 'hidden',
      }}
    >
      <div
        className="flex items-center gap-2.5 px-4 py-2.5"
        style={{
          borderBottom: '1px solid var(--color-border-subtle)',
          backgroundImage: 'radial-gradient(rgba(127,127,127,0.13) 1px, transparent 1px)',
          backgroundSize: '16px 16px',
        }}
      >
        <span
          className="flex items-center justify-center"
          style={{ width: 26, height: 26, borderRadius: 8, background: tint(ACCENT, 0.14) }}
        >
          <DiagramIcon name={diagram.nodes[0]?.icon} colour={ACCENT} />
        </span>
        <span className="text-[13px] font-semibold" style={{ color: 'var(--color-text)' }}>
          {diagram.title}
        </span>
        <span className="ml-auto text-[11.5px]" style={{ color: 'var(--color-text-tertiary)' }}>
          {diagram.nodes.length} {diagram.shape === 'flow' ? 'steps' : 'parts'}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 px-4 pt-4 pb-3">
        {steps.map((node, i) => {
          const mark = markColour(node.mark);
          return (
            <span key={i} className="flex items-center gap-1.5">
              {i > 0 ? (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-tertiary)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M5 12h14" /><path d="m13 6 6 6-6 6" />
                </svg>
              ) : null}
              <span
                className="flex items-center gap-1.5 text-[12px]"
                style={{
                  padding: '6px 12px',
                  borderRadius: 999,
                  background: mark ? tint(mark, 0.1) : 'var(--color-bg-secondary)',
                  border: `1px solid ${mark ? tint(mark, 0.28) : 'var(--color-border)'}`,
                  color: 'var(--color-text-secondary)',
                }}
              >
                <span style={{ width: 5, height: 5, borderRadius: 999, background: mark ?? 'var(--color-text-tertiary)' }} />
                {node.label}
              </span>
            </span>
          );
        })}
        {diagram.nodes.length > steps.length ? (
          <span className="text-[11.5px]" style={{ color: 'var(--color-text-tertiary)' }}>
            +{diagram.nodes.length - steps.length} more
          </span>
        ) : null}
      </div>

      <div className="flex items-center gap-3 px-4 pb-4">
        <button
          type="button"
          onClick={() => open(key, diagram)}
          className="flex items-center gap-2 text-[13px] font-semibold"
          style={{
            padding: '9px 16px',
            minHeight: 40,
            borderRadius: 'var(--radius-md)',
            background: 'var(--color-accent)',
            color: 'var(--color-on-accent)',
            border: 'none',
            cursor: 'pointer',
          }}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M15 3h6v6" /><path d="M10 14 21 3" /><path d="M21 14v7H3V3h7" />
          </svg>
          Open diagram
        </button>
      </div>
    </div>
  );
}
