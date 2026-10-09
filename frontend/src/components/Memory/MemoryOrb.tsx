/**
 * The Memory page's main view: every fact Sage remembers as a neural orb.
 *
 * About Sage is the glowing core; About you, Interests and Other are sectors
 * of the shell. Pinned facts glow and beat, facts learned in the last day send
 * out a ring, and signals run along the links. Click a fact for its panel
 * (pin, private, edit, forget with undo, linked facts); search lights up
 * matches; the legend flies to a region or hides it.
 *
 * Loaded lazily (three.js is its own chunk) and only while this tab is open;
 * unmounting frees the GPU context.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Eye, EyeOff, Pin, PinOff, Plus, Search, Trash2, X } from 'lucide-react';
import {
  addMemoryFact, deleteMemoryFact, listMemoryFacts, restoreMemoryFact, updateMemoryFact,
} from '../../lib/api';
import { REGIONS, REGION_COLORS, toGraphFact, type GraphFact, type RegionId } from '../../lib/memory-graph';
import { OrbScene } from './orb-scene';

// The orb is always drawn on black, in both themes, so its panels are too.
const ink = '#e7e7ea', faint = '#8b8b94';
const glass = {
  background: 'rgba(17,17,20,0.88)', border: '1px solid rgba(255,255,255,0.08)', backdropFilter: 'blur(8px)', color: ink,
} as const;
const field = { background: 'rgba(17,17,20,0.88)', border: '1px solid rgba(255,255,255,0.1)', color: ink } as const;

function OrbButton({ onClick, children, title, active, danger, disabled }: {
  onClick: () => void; children: React.ReactNode; title?: string; active?: boolean; danger?: boolean; disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs cursor-pointer disabled:opacity-40 disabled:cursor-default"
      style={{ ...field, color: danger ? '#f87171' : active ? '#22d3ee' : ink, borderColor: active ? 'rgba(34,211,238,0.6)' : field.border.split(' ').pop() }}
    >
      {children}
    </button>
  );
}

const sourceLabel = (s: string) => (s === 'you' ? 'you' : s === 'curated' ? 'curated' : 'from a conversation');
const day = (at: number) => (at ? new Date(at * 1000).toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' }) : '');

export default function MemoryOrb({ fail, focusId, onFocused }: {
  fail: (e: unknown) => void;
  /** Open on this fact (the Facts list's "View"), then report it done. */
  focusId?: string | null;
  onFocused?: () => void;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<OrbScene | null>(null);
  const [facts, setFacts] = useState<GraphFact[] | null>(null);
  const [query, setQuery] = useState('');
  const [hidden, setHidden] = useState<Set<RegionId>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hover, setHover] = useState<{ fact: GraphFact; x: number; y: number } | null>(null);
  const [newText, setNewText] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setFacts((await listMemoryFacts('')).map(toGraphFact));
    } catch (err) {
      fail(err);
    }
  }, [fail]);

  useEffect(() => { void load(); }, [load]);

  // The scene lives as long as this view; callbacks go through refs.
  const selectRef = useRef<(f: GraphFact | null) => void>(() => {});
  selectRef.current = (f) => { setSelectedId(f?.id ?? null); setEditing(null); sceneRef.current?.select(f?.id ?? null, true); };
  useEffect(() => {
    if (!stageRef.current || !labelsRef.current) return;
    let scene: OrbScene;
    try {
      scene = new OrbScene(stageRef.current, labelsRef.current, {
        onHover: (fact, x, y) => setHover(fact ? { fact, x, y } : null),
        onSelect: (fact) => selectRef.current(fact),
      });
    } catch (err) {
      fail(new Error(`The memory orb could not start (WebGL): ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    sceneRef.current = scene;
    return () => { scene.dispose(); sceneRef.current = null; };
  }, [fail]);

  useEffect(() => { if (facts) sceneRef.current?.setFacts(facts); }, [facts]);
  useEffect(() => {
    if (!focusId || !facts || !sceneRef.current) return;
    const fact = facts.find((f) => f.id === focusId);
    if (fact) selectRef.current(fact);
    onFocused?.();
  }, [focusId, facts, onFocused]);
  useEffect(() => { sceneRef.current?.setQuery(query); }, [query]);
  useEffect(() => { sceneRef.current?.setHidden(hidden); }, [hidden]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && selectedId && !editing) selectRef.current(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selectedId, editing]);

  const selected = facts?.find((f) => f.id === selectedId) ?? null;
  const linked = useMemo(
    () => (selected && facts ? sceneRef.current?.linked(selected.id) ?? [] : []),
    // facts: links are rebuilt when they change
    [selected, facts],
  );

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const forget = (f: GraphFact) => {
    selectRef.current(null);
    void act(() => deleteMemoryFact(f.id)).then(() => {
      toast('Forgot it. Restorable from Removed on the Facts tab.', {
        action: { label: 'Undo', onClick: () => void act(() => restoreMemoryFact(f.id)) },
      });
    });
  };

  const remember = () => {
    const text = newText.trim();
    if (!text) return;
    void act(() => addMemoryFact(text, true)).then(() => {
      setNewText('');
      toast('Remembered and pinned. Sage files it under a topic in a moment.');
      // Tagging runs on the memory worker; pick up the topic when it lands.
      window.setTimeout(() => void load(), 8000);
    });
  };

  const counts = useMemo(() => {
    const c: Record<RegionId, number> = { me: 0, sage: 0, interests: 0, other: 0 };
    for (const f of facts ?? []) c[f.region]++;
    return c;
  }, [facts]);
  const q = query.trim().toLowerCase();
  const matchCount = q && facts ? facts.filter((f) => f.text.toLowerCase().includes(q) || f.topic.toLowerCase().includes(q)).length : 0;

  return (
    <div className="relative w-full h-full rounded-xl overflow-hidden" style={{ background: '#0a0a0b', minHeight: 480 }}>
      <div ref={stageRef} className="absolute inset-0" />
      <div ref={labelsRef} className="absolute inset-0 pointer-events-none overflow-hidden" />
      <div className="absolute inset-0 pointer-events-none" style={{ background: 'radial-gradient(ellipse at 50% 50%, rgba(34,211,238,0.06) 0%, rgba(10,10,11,0) 55%)' }} />

      {/* search + remember */}
      <div className="absolute top-3 left-3 right-3 flex flex-wrap gap-2 items-center" style={{ maxWidth: selected ? 'calc(100% - 380px)' : undefined }}>
        <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg" style={glass}>
          <Search size={13} style={{ color: faint }} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search: lights up matches"
            className="bg-transparent outline-none text-sm w-48"
            style={{ color: ink }}
          />
          {q && <span className="text-xs" style={{ color: faint }}>{matchCount} match</span>}
        </div>
        <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg flex-1 min-w-[220px] max-w-md" style={glass}>
          <Plus size={13} style={{ color: faint }} />
          <input
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') remember(); }}
            placeholder="Tell Sage something to remember (pinned)"
            className="bg-transparent outline-none text-sm flex-1"
            style={{ color: ink }}
          />
        </div>
        <OrbButton onClick={remember} disabled={!newText.trim() || busy}>Remember</OrbButton>
      </div>

      {/* legend */}
      <div className="absolute left-3 bottom-3 rounded-xl px-3 py-2.5 text-sm" style={{ ...glass, minWidth: 210 }}>
        {(Object.keys(REGIONS) as RegionId[]).map((r) => {
          const off = hidden.has(r);
          return (
            <div key={r} className="flex items-center gap-2 py-0.5">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: REGION_COLORS[r], boxShadow: `0 0 8px ${REGION_COLORS[r]}` }} />
              <button className="flex-1 text-left cursor-pointer hover:underline" style={{ opacity: off ? 0.35 : 1, color: ink }} onClick={() => sceneRef.current?.flyToRegion(r)}>
                {REGIONS[r].name}
              </button>
              <span className="text-xs" style={{ color: faint, opacity: off ? 0.35 : 1 }}>{counts[r]}</span>
              <button
                className="text-xs cursor-pointer"
                style={{ color: faint }}
                onClick={() => setHidden((h) => { const n = new Set(h); if (n.has(r)) n.delete(r); else n.add(r); return n; })}
              >
                {off ? 'show' : 'hide'}
              </button>
            </div>
          );
        })}
        <div className="text-[11px] mt-1.5 pt-1.5" style={{ color: faint, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
          <span style={{ color: '#ffe9b0' }}>◉</span> pinned glows · <span style={{ color: '#22d3ee' }}>●</span> new pings
          <br />Click a region to fly there
        </div>
      </div>

      {facts && (
        <div className="absolute right-3 bottom-3 text-xs text-right" style={{ color: faint }}>
          {facts.length} memories · {facts.filter((f) => f.pinned).length} pinned · {facts.filter((f) => f.isNew).length} new today
          <br />drag to turn · scroll to zoom · Esc closes
        </div>
      )}
      {!facts && <div className="absolute inset-0 grid place-items-center text-sm" style={{ color: faint }}>Gathering memories…</div>}

      {hover && !selected && (
        <div
          className="fixed z-50 max-w-xs text-xs rounded-lg px-2.5 py-1.5 pointer-events-none"
          style={{ ...glass, left: Math.min(hover.x + 14, window.innerWidth - 330), top: hover.y + 14 }}
        >
          {hover.fact.pinned ? '📌 ' : ''}{hover.fact.text.length > 140 ? `${hover.fact.text.slice(0, 140)}…` : hover.fact.text}
        </div>
      )}

      {/* side panel */}
      {selected && (
        <aside className="absolute top-0 right-0 bottom-0 w-[360px] max-w-full overflow-y-auto p-5" style={{ ...glass, borderTop: 0, borderRight: 0, borderBottom: 0 }}>
          <button className="absolute top-3 right-3 p-1 rounded-lg cursor-pointer" style={{ color: faint }} title="Close (Esc)" onClick={() => selectRef.current(null)}>
            <X size={16} />
          </button>
          <div className="flex items-center gap-2 text-xs mr-8" style={{ color: faint }}>
            <span className="w-2 h-2 rounded-full" style={{ background: REGION_COLORS[selected.region] }} />
            {REGIONS[selected.region].name} › {selected.topic}
          </div>
          {editing !== null ? (
            <textarea
              autoFocus
              value={editing}
              onChange={(e) => setEditing(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Escape') setEditing(null); }}
              onBlur={() => {
                const text = editing.trim();
                setEditing(null);
                if (text && text !== selected.text) void act(() => updateMemoryFact(selected.id, { text }));
              }}
              className="w-full mt-3 mb-2 px-2 py-1.5 rounded-lg text-sm"
              style={field}
              rows={4}
            />
          ) : (
            <div className="text-[15px] leading-relaxed mt-3 mb-2 cursor-text" style={{ color: ink }} title="Click to edit" onClick={() => setEditing(selected.text)}>
              {selected.text}
            </div>
          )}
          <div className="flex flex-wrap gap-1.5 text-xs" style={{ color: faint }}>
            <span className="px-2 rounded-full" style={{ border: '1px solid rgba(255,255,255,0.1)' }}>{sourceLabel(selected.source)}</span>
            <span className="px-2 rounded-full" style={{ border: '1px solid rgba(255,255,255,0.1)' }}>{day(selected.createdAt)}</span>
            {selected.pinned && <span className="px-2 rounded-full" style={{ color: '#ffe9b0', border: '1px solid rgba(255,233,176,0.4)', boxShadow: '0 0 10px rgba(255,233,176,0.25)' }}>pinned · always in context</span>}
            {selected.private && <span className="px-2 rounded-full" style={{ border: '1px solid rgba(255,255,255,0.1)' }}>private</span>}
            {selected.isNew && <span className="px-2 rounded-full" style={{ color: '#22d3ee', border: '1px solid rgba(34,211,238,0.4)' }}>new</span>}
          </div>
          <div className="flex flex-wrap gap-2 my-4">
            <OrbButton active={selected.pinned} disabled={busy} title={selected.pinned ? 'Unpin' : 'Pin: always in context'} onClick={() => void act(() => updateMemoryFact(selected.id, { pinned: !selected.pinned }))}>
              {selected.pinned ? <PinOff size={12} /> : <Pin size={12} />} {selected.pinned ? 'Unpin' : 'Pin'}
            </OrbButton>
            <OrbButton disabled={busy} title={selected.private ? 'Private: never used when Sage speaks first' : 'Mark private'} onClick={() => void act(() => updateMemoryFact(selected.id, { private: !selected.private }))}>
              {selected.private ? <EyeOff size={12} /> : <Eye size={12} />} {selected.private ? 'Private' : 'Mark private'}
            </OrbButton>
            <OrbButton danger disabled={busy} title="Forget (restorable)" onClick={() => forget(selected)}>
              <Trash2 size={12} /> Forget
            </OrbButton>
          </div>
          <div className="text-[11px] uppercase tracking-wider mb-2" style={{ color: faint }}>Linked memories</div>
          {linked.length === 0 && <div className="text-xs" style={{ color: faint }}>Nothing linked yet.</div>}
          {linked.map(({ fact, score }) => (
            <button
              key={fact.id}
              className="block w-full text-left text-[13px] rounded-lg px-2.5 py-2 mb-1.5 cursor-pointer hover:border-cyan-400/50"
              style={{ border: '1px solid rgba(255,255,255,0.08)', color: ink }}
              onClick={() => selectRef.current(fact)}
            >
              {fact.text}
              <div className="text-[11px] mt-0.5" style={{ color: faint }}>
                {score !== null ? `says something similar · ${Math.round(score * 100)}%` : `same topic · ${fact.topic}`}
              </div>
            </button>
          ))}
        </aside>
      )}
    </div>
  );
}
