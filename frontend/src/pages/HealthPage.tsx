import { useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, Loader2, XCircle } from 'lucide-react';
import {
  applyFix,
  fetchFixPlan,
  fetchSystemHealth,
  type FixPlan,
  type HealthCheck,
  type HealthReport,
} from '../lib/api';
import { runBrowserChecks, type BrowserCheck } from '../lib/browser-health';

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

/**
 * A fix is always two deliberate steps: read the plan, then confirm. There is
 * no single click that changes anything, and the confirm button only appears
 * for fixes the server says it can actually perform.
 */
function FixControl({ fixId }: { fixId: string }) {
  const [plan, setPlan] = useState<FixPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const load = async () => {
    setBusy(true);
    setResult(null);
    try {
      setPlan(await fetchFixPlan(fixId));
      setFailed(false);
    } catch (err) {
      setFailed(true);
      setResult(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    setBusy(true);
    try {
      const outcome = await applyFix(fixId);
      setFailed(!outcome.applied);
      setResult(outcome.message + (outcome.detail ? ` ${outcome.detail}` : ''));
      setPlan(null);
    } catch (err) {
      setFailed(true);
      setResult(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2">
      {!plan && !result && (
        <button
          type="button"
          onClick={load}
          disabled={busy}
          className="rounded border border-border px-2 py-1 text-xs disabled:opacity-60"
        >
          {busy ? 'Loading…' : 'Show fix'}
        </button>
      )}

      {plan && (
        <div className="rounded-md border border-border p-3 text-xs">
          <p className="font-medium">{plan.title}</p>
          <p className="mt-1 text-muted-foreground">{plan.description}</p>
          {plan.steps.length > 0 && (
            <ul className="mt-2 list-disc pl-4 text-muted-foreground">
              {plan.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ul>
          )}
          {!plan.reversible && plan.automatic && (
            <p className="mt-2 text-amber-500">This cannot be undone.</p>
          )}
          <div className="mt-3 flex gap-2">
            {plan.automatic ? (
              <button
                type="button"
                onClick={confirm}
                disabled={busy}
                className="rounded bg-primary px-2 py-1 text-primary-foreground disabled:opacity-60"
              >
                {busy ? 'Applying…' : 'Confirm and apply'}
              </button>
            ) : (
              <span className="text-muted-foreground">
                This one has to be done by hand.
              </span>
            )}
            <button
              type="button"
              onClick={() => setPlan(null)}
              disabled={busy}
              className="rounded border border-border px-2 py-1 disabled:opacity-60"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {result && (
        <p className={`text-xs ${failed ? 'text-red-500' : 'text-emerald-500'}`}>
          {result}
        </p>
      )}
    </div>
  );
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
        {check.fix && <FixControl fixId={check.fix} />}
      </div>
    </li>
  );
}

export function HealthPage() {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [browser, setBrowser] = useState<BrowserCheck[] | null>(null);
  const [loading, setLoading] = useState<false | 'local' | 'live'>(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (live: boolean) => {
    setLoading(live ? 'live' : 'local');
    setError(null);
    try {
      // The browser half runs alongside the server's: a muted or denied
      // microphone is invisible from the server, and it is the failure that
      // started this milestone.
      const [serverReport, browserChecks] = await Promise.all([
        fetchSystemHealth(live),
        runBrowserChecks(),
      ]);
      setReport(serverReport);
      setBrowser(browserChecks);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const problems =
    report?.sections.flatMap((s) => s.checks.filter((c) => c.status !== 'ok')) ?? [];
  const total = report?.sections.reduce((n, s) => n + s.checks.length, 0) ?? 0;
  // The headline must not read "1 of 61" with a browser failure sitting
  // uncounted underneath it. Browser checks stay visually separate, because
  // they are evidence about this browser rather than the server, but they
  // still count towards the summary and the icon.
  const browserProblems = browser?.filter((c) => c.status !== 'ok') ?? [];
  const headlineStatus =
    browserProblems.some((c) => c.status === 'fail') || report?.status === 'fail'
      ? 'fail'
      : browserProblems.length > 0 || report?.status === 'warn'
        ? 'warn'
        : 'ok';

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
            <StatusIcon status={headlineStatus} />
            <span className="text-sm">
              {problems.length === 0
                ? `All ${total} checks passed.`
                : `${problems.length} of ${total} checks need attention.`}
              {browserProblems.length > 0 &&
                ` Plus ${browserProblems.length} in this browser.`}
            </span>
          </div>

          {browser && browser.length > 0 && (
            <section className="mb-6">
              <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                This browser
              </h2>
              <p className="mb-1 text-xs text-muted-foreground/80">
                Read from this browser, not the server. Another browser or
                device can differ.
              </p>
              <ul>
                {browser.map((check) => (
                  <li
                    key={check.name}
                    className="flex gap-3 py-2 border-b border-border/40 last:border-0"
                  >
                    <StatusIcon status={check.status} />
                    <div className="min-w-0 flex-1">
                      <span className="text-sm font-medium">{check.name}</span>
                      <p className="text-sm text-muted-foreground break-words">
                        {check.message}
                      </p>
                      {check.details && (
                        <p className="mt-1 text-xs text-muted-foreground/80 break-words">
                          {check.details}
                        </p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

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
