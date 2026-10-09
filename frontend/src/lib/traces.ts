/**
 * Traces as the server sends them (GET /v1/traces), and the small readings
 * the Trace Debugger shows.
 *
 * The debugger was written for another shape (`duration_ms` and `data` on
 * each step): every duration read "NaNms" and clicking a trace crashed the
 * app with "Cannot convert undefined or null to object" (9 October). Every
 * field is optional here, because a trace from an older server may lack any.
 */
export interface TraceStep {
  step_type: string;
  timestamp?: number;
  duration_seconds?: number;
  input?: Record<string, unknown>;
  output?: Record<string, unknown> & { success?: boolean };
  metadata?: Record<string, unknown>;
}

export interface Trace {
  id: string;
  query?: string;
  model?: string;
  engine?: string;
  steps?: TraceStep[];
  result?: string;
  created_at?: string;
  duration_ms?: number;
  total_tokens?: number;
}

export function stepSeconds(step: TraceStep): number {
  const value = Number(step.duration_seconds);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

/** The whole request's time: the server's figure, else the longest step. */
export function traceSeconds(trace: Trace): number {
  const ms = Number(trace.duration_ms);
  if (Number.isFinite(ms) && ms > 0) return ms / 1000;
  return Math.max(0, ...(trace.steps ?? []).map(stepSeconds));
}

export function failedTools(trace: Trace): number {
  return (trace.steps ?? []).filter(
    (s) => s.step_type === 'tool_call' && s.output?.success === false,
  ).length;
}

/** One line for a step's row: the tool's name, the tokens, the reply. */
export function stepLabel(step: TraceStep): string {
  const input = step.input ?? {};
  const output = step.output ?? {};
  if (step.step_type === 'tool_call') return String(input.tool ?? 'tool');
  if (step.step_type === 'generate') {
    const tokens = Number(output.tokens);
    const rounds = Number(output.rounds);
    const parts = [];
    if (input.model) parts.push(String(input.model));
    if (Number.isFinite(rounds) && rounds > 0) parts.push(`${rounds} rounds`);
    if (Number.isFinite(tokens) && tokens > 0) parts.push(`${tokens.toLocaleString()} tokens`);
    return parts.join(' · ');
  }
  if (step.step_type === 'respond') {
    const text = String(output.content ?? '').replace(/\s+/g, ' ');
    return text.length > 60 ? `${text.slice(0, 60)}…` : text;
  }
  return '';
}
