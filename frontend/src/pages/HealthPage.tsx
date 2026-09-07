import { useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, Loader2, XCircle } from 'lucide-react';
import { fetchSystemHealth, type HealthCheck, type HealthReport } from '../lib/api';

/**
 * Health page — pull-only by explicit decision. Nothing runs until the user
 * presses the button, and the live probes stay behind a second, separate
 * button because they are billable and count against a daily cap.
 */

const STATUS_STYLES: Record<string, { icon: typeof CheckCircle2; className: string }> = {
  ok: { icon: CheckCircle2, className: 'text-emerald-500' },
  warn: { icon: AlertTriangle, className: 'text-amber-500' },
  fail: { icon: XCircle, className: 'text-red-500' },
};

function StatusIcon({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.warn;
  const Icon = style.icon;
  return <Icon className={`h-4 w-4 shrink-0 ${style.className}`} aria-hidden />;
}

function CheckRow({ check }: { check: HealthCheck }) {
  return (
    <li className="flex gap-3 py-2 border-b border-border/40 last:border-0">
      <StatusIcon status={check.status} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-sm font-medium">{check.name}</span>
          {check.live && (
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              live
            </span>
          )}
        </div>
        <p className="text-sm text-muted-foreground break-words">{check.message}</p>
        {check.details && (
          <p className="mt-1 text-xs text-muted-foreground/80 break-words">
            {check.details}
          </p>
        )}
      </div>
    </li>
  );
}

export function HealthPage() {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [loading, setLoading] = useState<false | 'local' | 'live'>(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (live: boolean) => {
    setLoading(live ? 'live' : 'local');
    setError(null);
    try {
      setReport(await fetchSystemHealth(live));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const problems =
    report?.sections.flatMap((s) => s.checks.filter((c) => c.status !== 'ok')) ?? [];
  const total = report?.sections.reduce((n, s) => n + s.checks.length, 0) ?? 0;

  return (
    // main is `overflow-hidden h-full`, so a page that does not claim
    // `flex-1 overflow-y-auto` simply cannot scroll past the fold.
    <div className="flex-1 overflow-y-auto px-6 py-10">
      <div className="mx-auto w-full max-w-3xl">
      <header className="mb-6">
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <Activity className="h-5 w-5" aria-hidden />
          Health
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Sage checks itself only when asked. A live check also probes paid
          providers, which counts against their daily caps.
        </p>
      </header>

      <div className="mb-6 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => run(false)}
          disabled={loading !== false}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
        >
          {loading === 'local' && <Loader2 className="h-4 w-4 animate-spin" />}
          Run check
        </button>
        <button
          type="button"
          onClick={() => run(true)}
          disabled={loading !== false}
          className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm font-medium disabled:opacity-60"
        >
          {loading === 'live' && <Loader2 className="h-4 w-4 animate-spin" />}
          Run live check
        </button>
      </div>

      {error && (
        <p className="mb-4 rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-500">
          {error}
        </p>
      )}

      {!report && !error && (
        <p className="text-sm text-muted-foreground">
          No check has run yet.
        </p>
      )}

      {report && (
        <>
          <div className="mb-6 flex items-center gap-2 rounded-md border border-border p-3">
            <StatusIcon status={report.status} />
            <span className="text-sm">
              {problems.length === 0
                ? `All ${total} checks passed.`
                : `${problems.length} of ${total} checks need attention.`}
            </span>
          </div>

          {report.sections.map((section) => (
            <section key={section.id} className="mb-6">
              <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                {section.label}
              </h2>
              <ul>
                {section.checks.map((check) => (
                  <CheckRow key={`${section.id}:${check.name}`} check={check} />
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
      </div>
    </div>
  );
}
