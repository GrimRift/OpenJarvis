/**
 * Memory (M38): everything Sage remembers, in one place.
 *
 * Facts (search / add / edit / delete / pin / private), the daily diary,
 * indexed documents with upload, and the profile. Fact search uses the same
 * ranking recall does, so what this page shows is what the model can reach.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Brain, Pin, PinOff, EyeOff, Eye, Trash2, RotateCcw, Upload, RefreshCw, Search, Plus } from 'lucide-react';
import {
  addMemoryFact,
  deleteMemoryDocument,
  deleteMemoryEpisode,
  deleteMemoryFact,
  getMemoryPageSettings,
  getMemoryProfile,
  indexMemoryPath,
  listHygieneRuns,
  listMemoryDocuments,
  listMemoryEpisodes,
  listMemoryFacts,
  putMemoryPageSettings,
  putMemoryProfile,
  restoreMemoryFact,
  rewriteMemoryEpisode,
  runHygieneNow,
  storeMemory,
  updateMemoryEpisode,
  updateMemoryFact,
  uploadMemoryDocument,
  type HygieneRun,
  type MemoryDocument,
  type MemoryEpisode,
  type MemoryFact,
  type MemoryPageSettings,
} from '../lib/api';

type Tab = 'facts' | 'episodes' | 'documents' | 'profile';

const panel = { background: 'var(--color-surface)', border: '1px solid var(--color-border)' } as const;
const input = {
  background: 'var(--color-bg-secondary)',
  color: 'var(--color-text)',
  border: '1px solid var(--color-border)',
} as const;
const muted = { color: 'var(--color-text-tertiary)' } as const;

function stamp(at: number): string {
  return new Date(at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function Button({ onClick, children, title, danger, disabled }: {
  onClick: () => void; children: React.ReactNode; title?: string; danger?: boolean; disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs cursor-pointer disabled:opacity-40 disabled:cursor-default"
      style={{ ...input, color: danger ? 'var(--color-error, #ef4444)' : 'var(--color-text-secondary)' }}
    >
      {children}
    </button>
  );
}

export function MemoryPage() {
  const [tab, setTab] = useState<Tab>('facts');
  const [error, setError] = useState<string | null>(null);
  const fail = (err: unknown) => setError(err instanceof Error ? err.message : String(err));

  return (
    <div className="flex-1 overflow-y-auto px-6 py-10">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3 mb-1">
          <Brain size={22} style={{ color: 'var(--color-accent)' }} />
          <h1 className="text-xl font-semibold" style={{ color: 'var(--color-text)' }}>Memory</h1>
        </div>
        <p className="text-sm mb-6" style={{ color: 'var(--color-text-secondary)' }}>
          What Sage remembers about you, and how it gets it back. Facts are chosen for each reply by relevance; pinned ones always go.
        </p>

        <div className="flex gap-1 mb-6 border-b" style={{ borderColor: 'var(--color-border)' }}>
          {(['facts', 'episodes', 'documents', 'profile'] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className="px-4 py-2 text-sm capitalize cursor-pointer"
              style={{
                color: tab === t ? 'var(--color-text)' : 'var(--color-text-tertiary)',
                borderBottom: tab === t ? '2px solid var(--color-accent)' : '2px solid transparent',
              }}
            >
              {t}
            </button>
          ))}
        </div>

        {error && (
          <div className="mb-4 text-xs px-3 py-2 rounded-lg" style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-error, #ef4444)' }}>
            {error} <button className="underline ml-2 cursor-pointer" onClick={() => setError(null)}>dismiss</button>
          </div>
        )}

        {tab === 'facts' && <FactsTab fail={fail} />}
        {tab === 'episodes' && <EpisodesTab fail={fail} />}
        {tab === 'documents' && <DocumentsTab fail={fail} />}
        {tab === 'profile' && <ProfileTab fail={fail} />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function FactsTab({ fail }: { fail: (e: unknown) => void }) {
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [removed, setRemoved] = useState<MemoryFact[]>([]);
  const [query, setQuery] = useState('');
  const [showRemoved, setShowRemoved] = useState(false);
  const [newText, setNewText] = useState('');
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [settings, setSettings] = useState<MemoryPageSettings | null>(null);
  const [runs, setRuns] = useState<HygieneRun[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setFacts(await listMemoryFacts(query));
      setRemoved(await listMemoryFacts('', true));
    } catch (err) {
      fail(err);
    }
  }, [query, fail]);

  useEffect(() => {
    const timer = setTimeout(() => void refresh(), 200);
    return () => clearTimeout(timer);
  }, [refresh]);
  useEffect(() => {
    getMemoryPageSettings().then(setSettings).catch(() => setSettings(null));
    listHygieneRuns().then(setRuns).catch(() => setRuns([]));
  }, []);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      await refresh();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const pending = useMemo(() => facts.filter((f) => f.pending).length, [facts]);
  const lastRun = runs[0];

  return (
    <div className="space-y-4">
      <div className="rounded-xl p-4 flex flex-wrap gap-3 items-center" style={panel}>
        <div className="flex items-center gap-2 flex-1 min-w-[240px]">
          <Search size={14} style={muted} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search as Sage recalls: try 'capstone' or 'browser'"
            className="flex-1 px-2 py-1.5 rounded-lg text-sm"
            style={input}
          />
        </div>
        <span className="text-xs" style={muted}>
          {facts.length} fact{facts.length === 1 ? '' : 's'}{query ? ' matching' : ''}
          {pending ? ` · ${pending} learned in the last day` : ''}
        </span>
        <Button onClick={() => setShowRemoved((v) => !v)}>
          <RotateCcw size={12} /> {showRemoved ? 'Hide removed' : `Removed (${removed.length})`}
        </Button>
      </div>

      <div className="rounded-xl p-4 flex gap-2 items-center" style={panel}>
        <Plus size={14} style={muted} />
        <input
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && newText.trim()) {
              void act(() => addMemoryFact(newText.trim(), true)).then(() => setNewText(''));
            }
          }}
          placeholder="Tell Sage something to remember (pinned: always in context)"
          className="flex-1 px-2 py-1.5 rounded-lg text-sm"
          style={input}
        />
        <Button onClick={() => void act(() => addMemoryFact(newText.trim(), true)).then(() => setNewText(''))} disabled={!newText.trim() || busy}>
          Remember
        </Button>
      </div>

      {showRemoved && (
        <div className="rounded-xl p-4" style={panel}>
          <div className="text-sm mb-2" style={{ color: 'var(--color-text)' }}>Removed — restorable for {settings?.restore_window_days ?? 7} days</div>
          {removed.length === 0 && <div className="text-xs" style={muted}>Nothing removed.</div>}
          {removed.map((f) => (
            <div key={f.id} className="flex items-start justify-between gap-3 py-2 text-sm" style={{ borderTop: '1px solid var(--color-border-subtle)' }}>
              <div>
                <div style={{ color: 'var(--color-text-secondary)' }}>{f.text}</div>
                <div className="text-xs" style={muted}>{f.removed_reason} · {f.removed_at ? stamp(f.removed_at) : ''}</div>
              </div>
              <Button onClick={() => void act(() => restoreMemoryFact(f.id))}><RotateCcw size={12} /> Restore</Button>
            </div>
          ))}
        </div>
      )}

      <div className="rounded-xl" style={panel}>
        {facts.length === 0 && <div className="p-4 text-sm" style={muted}>{query ? 'Nothing matches.' : 'No facts yet.'}</div>}
        {facts.map((f) => (
          <div key={f.id} className="flex items-start justify-between gap-3 px-4 py-3" style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>
            <div className="flex-1 min-w-0">
              {editing?.id === f.id ? (
                <textarea
                  autoFocus
                  value={editing.text}
                  onChange={(e) => setEditing({ id: f.id, text: e.target.value })}
                  onBlur={() => {
                    const text = editing.text.trim();
                    setEditing(null);
                    if (text && text !== f.text) void act(() => updateMemoryFact(f.id, { text }));
                  }}
                  className="w-full px-2 py-1 rounded-lg text-sm"
                  style={input}
                  rows={2}
                />
              ) : (
                <div className="text-sm cursor-text" style={{ color: 'var(--color-text)' }} onClick={() => setEditing({ id: f.id, text: f.text })} title="Click to edit">
                  {f.text}
                </div>
              )}
              <div className="text-xs mt-0.5 flex gap-2 flex-wrap" style={muted}>
                <span>{f.source === 'you' ? 'you' : f.source === 'curated' ? 'curated' : 'from a conversation'}</span>
                {f.day && <span>· {f.day}</span>}
                {f.pinned && <span>· pinned</span>}
                {f.private && <span>· private</span>}
                {f.pending && <span style={{ color: 'var(--color-accent)' }}>· new</span>}
                {typeof f.score === 'number' && <span>· relevance {f.score.toFixed(1)}</span>}
              </div>
            </div>
            <div className="flex gap-1 shrink-0">
              <Button title={f.pinned ? 'Unpin' : 'Pin: always in context'} onClick={() => void act(() => updateMemoryFact(f.id, { pinned: !f.pinned }))}>
                {f.pinned ? <PinOff size={12} /> : <Pin size={12} />}
              </Button>
              <Button title={f.private ? 'Private: never used when Sage speaks first' : 'Mark private'} onClick={() => void act(() => updateMemoryFact(f.id, { private: !f.private }))}>
                {f.private ? <EyeOff size={12} /> : <Eye size={12} />}
              </Button>
              <Button title="Forget (restorable)" danger onClick={() => void act(() => deleteMemoryFact(f.id))}>
                <Trash2 size={12} />
              </Button>
            </div>
          </div>
        ))}
      </div>

      <div className="rounded-xl p-4 space-y-3" style={panel}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm" style={{ color: 'var(--color-text)' }}>Learning from conversations</div>
            <div className="text-xs" style={muted}>Which model extracts facts after each exchange. Cloud produces far fewer stale and duplicate facts; local keeps it on this machine.</div>
          </div>
          <select
            value={settings?.extraction_mode ?? 'cloud'}
            disabled={!settings}
            onChange={(e) => putMemoryPageSettings({ extraction_mode: e.target.value as 'cloud' | 'local' }).then(setSettings).catch(fail)}
            className="px-2 py-1 rounded-lg text-sm"
            style={input}
          >
            <option value="cloud">Cloud ({settings?.cloud_model ?? 'gpt-5.6-luna'})</option>
            <option value="local">Local</option>
          </select>
        </div>
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm" style={{ color: 'var(--color-text)' }}>Nightly clean-up</div>
            <div className="text-xs" style={muted}>
              Merges duplicates, resolves contradictions (newest wins), expires facts that were only true on the day they were written. Applied overnight; every removal is restorable above.
              {lastRun && (
                <> Last run {stamp(lastRun.at)}: {lastRun.facts_before} → {lastRun.facts_after} facts, {lastRun.changes.length} change{lastRun.changes.length === 1 ? '' : 's'}{lastRun.error ? ` (failed: ${lastRun.error})` : ''}.</>
              )}
            </div>
          </div>
          <div className="flex gap-1">
            <Button onClick={() => putMemoryPageSettings({ hygiene_enabled: !settings?.hygiene_enabled }).then(setSettings).catch(fail)} disabled={!settings}>
              {settings?.hygiene_enabled ? 'On' : 'Off'}
            </Button>
            <Button onClick={() => void act(async () => { await runHygieneNow(); setRuns(await listHygieneRuns()); })} disabled={busy || !settings?.hygiene_enabled}>
              <RefreshCw size={12} /> Run now
            </Button>
          </div>
        </div>
        {lastRun && lastRun.changes.length > 0 && (
          <div className="text-xs space-y-1" style={muted}>
            {lastRun.changes.slice(0, 8).map((c, i) => (
              <div key={i}>
                <span className="capitalize">{c.kind}</span>
                {c.kept_text ? `: kept "${c.kept_text.slice(0, 80)}"` : ''}
                {c.removed.length ? ` — removed ${c.removed.map((r) => `"${r.text.slice(0, 60)}"`).join(', ')}` : ''}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function EpisodesTab({ fail }: { fail: (e: unknown) => void }) {
  const [episodes, setEpisodes] = useState<MemoryEpisode[]>([]);
  const [editing, setEditing] = useState<{ day: string; summary: string } | null>(null);
  const [busyDay, setBusyDay] = useState<string | null>(null);
  const refresh = useCallback(() => listMemoryEpisodes().then(setEpisodes).catch(fail), [fail]);
  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <div className="space-y-3">
      <p className="text-xs" style={muted}>One entry per day with conversations, written nightly (or on boot). The last two days are in every prompt as "recent days".</p>
      {episodes.length === 0 && <div className="rounded-xl p-4 text-sm" style={{ ...panel, ...muted }}>No episodes yet.</div>}
      {episodes.map((e) => (
        <div key={e.day} className="rounded-xl p-4" style={panel}>
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm font-medium" style={{ color: 'var(--color-text)' }}>{e.day} <span className="text-xs font-normal" style={muted}>· {e.turns} turns · {e.model}</span></div>
            <div className="flex gap-1">
              <Button disabled={busyDay === e.day} onClick={() => { setBusyDay(e.day); rewriteMemoryEpisode(e.day).then(refresh).catch(fail).finally(() => setBusyDay(null)); }}>
                <RefreshCw size={12} /> Rewrite
              </Button>
              <Button danger onClick={() => deleteMemoryEpisode(e.day).then(refresh).catch(fail)}><Trash2 size={12} /></Button>
            </div>
          </div>
          {editing?.day === e.day ? (
            <textarea
              autoFocus
              value={editing.summary}
              onChange={(ev) => setEditing({ day: e.day, summary: ev.target.value })}
              onBlur={() => {
                const summary = editing.summary.trim();
                setEditing(null);
                if (summary && summary !== e.summary) updateMemoryEpisode(e.day, summary).then(refresh).catch(fail);
              }}
              className="w-full px-2 py-1 rounded-lg text-sm"
              style={input}
              rows={5}
            />
          ) : (
            <div className="text-sm leading-relaxed cursor-text" style={{ color: 'var(--color-text-secondary)' }} onClick={() => setEditing({ day: e.day, summary: e.summary })} title="Click to edit">
              {e.summary}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------

function DocumentsTab({ fail }: { fail: (e: unknown) => void }) {
  const [docs, setDocs] = useState<MemoryDocument[]>([]);
  const [path, setPath] = useState('');
  const [text, setText] = useState('');
  const [note, setNote] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const refresh = useCallback(() => listMemoryDocuments().then(setDocs).catch(fail), [fail]);
  useEffect(() => { void refresh(); }, [refresh]);

  const upload = async (file: File) => {
    try {
      const result = await uploadMemoryDocument(file);
      setNote(`${result.source}: ${result.chunks} chunk${result.chunks === 1 ? '' : 's'} indexed${result.note ? ` (${result.note})` : ''}`);
      await refresh();
    } catch (err) {
      fail(err);
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-xl p-4 space-y-2" style={panel}>
          <div className="text-sm" style={{ color: 'var(--color-text)' }}>Upload a file</div>
          <div className="text-xs" style={muted}>.md, .txt, .csv, .pdf, .docx — up to 20 MB. Indexed for retrieval; Sage cites it when relevant.</div>
          <input ref={fileRef} type="file" accept=".md,.txt,.csv,.pdf,.docx" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ''; }} />
          <Button onClick={() => fileRef.current?.click()}><Upload size={12} /> Choose file</Button>
          {note && <div className="text-xs" style={muted}>{note}</div>}
        </div>
        <div className="rounded-xl p-4 space-y-2" style={panel}>
          <div className="text-sm" style={{ color: 'var(--color-text)' }}>Index a folder</div>
          <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\\Users\\you\\Documents\\notes" className="w-full px-2 py-1.5 rounded-lg text-sm" style={input} />
          <Button disabled={!path.trim()} onClick={() => indexMemoryPath(path.trim()).then((r) => { setNote(`${r.chunks_indexed} chunks indexed`); return refresh(); }).catch(fail)}>Index</Button>
        </div>
      </div>
      <div className="rounded-xl p-4 space-y-2" style={panel}>
        <div className="text-sm" style={{ color: 'var(--color-text)' }}>Store text</div>
        <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste anything Sage should be able to find later" className="w-full px-2 py-1.5 rounded-lg text-sm" style={input} rows={3} />
        <Button disabled={!text.trim()} onClick={() => storeMemory(text.trim()).then(() => { setText(''); return refresh(); }).catch(fail)}>Store</Button>
      </div>
      <div className="rounded-xl" style={panel}>
        {docs.length === 0 && <div className="p-4 text-sm" style={muted}>Nothing indexed yet.</div>}
        {docs.map((d) => (
          <div key={d.source} className="flex items-start justify-between gap-3 px-4 py-3" style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>
            <div className="min-w-0">
              <div className="text-sm truncate" style={{ color: 'var(--color-text)' }}>{d.source}</div>
              <div className="text-xs" style={muted}>{d.chunks} chunk{d.chunks === 1 ? '' : 's'} · {d.preview}</div>
            </div>
            <Button danger title="Remove from memory" onClick={() => deleteMemoryDocument(d.source === '(pasted text)' ? '' : d.source).then(refresh).catch(fail)}><Trash2 size={12} /></Button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function ProfileTab({ fail }: { fail: (e: unknown) => void }) {
  const [text, setText] = useState('');
  const [saved, setSaved] = useState('');
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    getMemoryProfile().then((r) => { setText(r.text); setSaved(r.text); }).catch(fail);
  }, [fail]);
  return (
    <div className="space-y-3">
      <p className="text-xs" style={muted}>USER.md — who you are, in your words. It is in every prompt, so keep it to what should always be true. A backup is kept on each save.</p>
      <textarea
        value={text}
        onChange={(e) => { setText(e.target.value); setDirty(e.target.value !== saved); }}
        className="w-full px-3 py-2 rounded-xl text-sm font-mono leading-relaxed"
        style={{ ...input, minHeight: 420 }}
      />
      <div className="flex gap-2">
        <Button disabled={!dirty} onClick={() => putMemoryProfile(text).then(() => { setSaved(text); setDirty(false); }).catch(fail)}>Save</Button>
        <Button disabled={!dirty} onClick={() => { setText(saved); setDirty(false); }}>Revert</Button>
      </div>
    </div>
  );
}
