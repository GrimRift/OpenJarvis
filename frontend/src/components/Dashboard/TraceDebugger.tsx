import { useState, useEffect, useCallback } from 'react';
import { GitBranch, Clock, ChevronRight, ChevronDown, Check, X } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import {
  type Trace,
  type TraceStep,
  failedTools,
  stepLabel,
  stepSeconds,
  traceSeconds,
} from '../../lib/traces';

const STEP_COLORS: Record<string, string> = {
  route: 'var(--color-accent)',
  retrieve: 'var(--color-success)',
  generate: 'var(--color-warning)',
  tool_call: 'var(--color-accent-purple)',
  respond: 'var(--color-accent)',
};

function seconds(value: number): string {
  return value >= 10 ? `${value.toFixed(0)} s` : `${value.toFixed(1)} s`;
}

function StepBadge({ type }: { type: string }) {
  const color = STEP_COLORS[type] || 'var(--color-text-tertiary)';
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium"
      style={{ background: `color-mix(in srgb, ${color} 15%, transparent)`, color }}
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />
      {type.replace('_', ' ')}
    </span>
  );
}

function TraceCard({ trace, isActive, onClick }: { trace: Trace; isActive: boolean; onClick: () => void }) {
  const failed = failedTools(trace);
  const tools = (trace.steps ?? []).filter((s) => s.step_type === 'tool_call').length;
  return (
    <button
      onClick={onClick}
      className="w-full text-left p-3 rounded-lg transition-colors cursor-pointer"
      style={{
        background: isActive ? 'var(--color-bg-tertiary)' : 'transparent',
        border: isActive ? '1px solid var(--color-border)' : '1px solid transparent',
      }}
    >
      <div className="text-sm truncate mb-1" style={{ color: 'var(--color-text)' }}>
        {trace.query || 'Untitled query'}
      </div>
      <div className="flex items-center gap-2 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
        <span>{seconds(traceSeconds(trace))}</span>
        <span>&middot;</span>
        <span>{tools} {tools === 1 ? 'tool' : 'tools'}</span>
        {failed > 0 && (
          <span style={{ color: 'var(--color-error, #ef4444)' }}>&middot; {failed} failed</span>
        )}
        <span>&middot;</span>
        <span>{trace.created_at ? new Date(trace.created_at).toLocaleTimeString() : ''}</span>
      </div>
    </button>
  );
}

function Field({ name, value }: { name: string; value: unknown }) {
  if (value === undefined || value === null || value === '') return null;
  let text = typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  if (name === 'arguments') {
    // Arguments arrive as a JSON string; shown pretty when they parse.
    try {
      text = JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      // Not JSON: shown as it is.
    }
  }
  return (
    <div className="py-1">
      <div className="font-mono text-[11px] mb-0.5" style={{ color: 'var(--color-text-tertiary)' }}>
        {name}
      </div>
      <pre
        className="whitespace-pre-wrap break-words text-xs m-0"
        style={{ color: 'var(--color-text-secondary)', fontFamily: 'inherit' }}
      >
        {text}
      </pre>
    </div>
  );
}

function StepDetail({ step, index }: { step: TraceStep; index: number }) {
  const [expanded, setExpanded] = useState(step.step_type === 'tool_call' && step.output?.success === false);
  const isTool = step.step_type === 'tool_call';
  const ok = step.output?.success;
  const input = step.input ?? {};
  const output = step.output ?? {};
  const meta = step.metadata ?? {};

  return (
    <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--color-border)' }}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors cursor-pointer"
        style={{ background: 'var(--color-bg-secondary)' }}
      >
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="text-xs font-mono" style={{ color: 'var(--color-text-tertiary)' }}>
          {index + 1}
        </span>
        <StepBadge type={step.step_type} />
        <span className="text-xs truncate" style={{ color: 'var(--color-text-secondary)' }}>
          {stepLabel(step)}
        </span>
        {isTool && ok !== undefined && (
          ok ? (
            <Check size={13} style={{ color: 'var(--color-success)' }} aria-label="succeeded" />
          ) : (
            <X size={13} style={{ color: 'var(--color-error, #ef4444)' }} aria-label="failed" />
          )
        )}
        <span className="flex-1" />
        <span className="text-xs font-mono flex items-center gap-1" style={{ color: 'var(--color-text-tertiary)' }}>
          <Clock size={10} />
          {seconds(stepSeconds(step))}
        </span>
      </button>
      {expanded && (
        <div className="px-3 py-2 text-xs" style={{ borderTop: '1px solid var(--color-border)' }}>
          {isTool ? (
            <>
              <Field name="tool" value={input.tool} />
              <Field name="arguments" value={input.arguments} />
              {ok === false && <Field name="error" value={output.error ?? output.result} />}
              {ok !== false && <Field name="result (start)" value={output.result} />}
              {Object.keys(meta).length > 0 && <Field name="notes" value={meta} />}
            </>
          ) : step.step_type === 'respond' ? (
            <Field name="reply" value={output.content} />
          ) : (
            <>
              {Object.entries(input).map(([k, v]) => <Field key={`i-${k}`} name={k} value={v} />)}
              {Object.entries(output).map(([k, v]) => <Field key={`o-${k}`} name={k} value={v} />)}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function TraceDebugger() {
  const [traces, setTraces] = useState<Trace[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchTraces = useCallback(async () => {
    try {
      // apiFetch, not fetch: a bare call sends no Authorization header and
      // 401s whenever the server has a key set, which read as "no traces".
      const res = await apiFetch('/v1/traces?limit=50');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTraces(Array.isArray(data.traces) ? data.traces : []);
      setError(null);
    } catch (err) {
      setError(`Cannot load traces (${err instanceof Error ? err.message : 'unknown'})`);
    }
  }, []);

  useEffect(() => {
    fetchTraces();
  }, [fetchTraces]);

  const selected = traces.find((t) => t.id === selectedId);

  return (
    <div className="hud-panel p-6">
      <h3 className="hud-label flex items-center gap-2 mb-4">
        <GitBranch size={12} style={{ color: 'var(--color-accent)' }} />
        Trace Debugger
        <button
          onClick={fetchTraces}
          className="ml-auto text-[11px] cursor-pointer"
          style={{ color: 'var(--color-text-tertiary)' }}
        >
          Refresh
        </button>
      </h3>

      {error ? (
        <div className="h-48 flex items-center justify-center text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
          <span className="hud-mono">{error}</span>
        </div>
      ) : traces.length === 0 ? (
        <div className="h-48 flex items-center justify-center text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
          No traces yet. Start making queries to see them here.
        </div>
      ) : (
        <div className="flex gap-4 h-96">
          <div className="w-1/3 overflow-y-auto flex flex-col gap-1 pr-2" style={{ borderRight: '1px solid var(--color-border)' }}>
            {traces.map((trace) => (
              <TraceCard
                key={trace.id}
                trace={trace}
                isActive={trace.id === selectedId}
                onClick={() => setSelectedId(trace.id)}
              />
            ))}
          </div>

          <div className="flex-1 overflow-y-auto">
            {selected ? (
              <div className="flex flex-col gap-2">
                <div className="text-sm font-medium" style={{ color: 'var(--color-text)' }}>
                  {selected.query}
                </div>
                <div className="text-[11px] mb-2" style={{ color: 'var(--color-text-tertiary)' }}>
                  {[selected.model, selected.engine].filter(Boolean).join(' · ')}
                  {' · '}
                  {seconds(traceSeconds(selected))}
                  {selected.total_tokens ? ` · ${selected.total_tokens.toLocaleString()} tokens` : ''}
                </div>
                {(selected.steps ?? []).map((step, i) => (
                  <StepDetail key={i} step={step} index={i} />
                ))}
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
                Select a trace to view details
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
