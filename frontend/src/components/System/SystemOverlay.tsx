/**
 * The system panel (M41), design "D1" the user picked on 1 Oct: Sage's line,
 * four rings, rows with bars and a last-minute graph drawn as linked glowing
 * points, the programs using the most (Sage's own marked) and network/power,
 * over a faint drifting particle web. A part running high breathes amber; the
 * part the question was about ("how's my memory?") is outlined.
 *
 * Live: it polls /v1/system/stats once a second while open and stops when
 * closed. Programs other than Sage's and Windows' have an End button, which
 * only acts after the user presses Confirm -- the model cannot end anything.
 */

import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { endProgram, useSystemStats } from '../../lib/system-api';
import { isBackdropClick } from '../../lib/overlay-backdrop';
import { isHigh, uptimeLabel, type SystemProgram, type SystemReport } from '../../lib/system-report';
import { ACCENT, AMBER, DIM, MUTED, SYSTEM_STYLE } from '../../lib/system-style';
import { ParticleWeb } from './ParticleWeb';
import { Bar, Constellation, Ring } from './SystemParts';

const CARD = 'rgba(22, 22, 26, 0.82)';

function ProgramRow({ p, onEnded }: { p: SystemProgram; onEnded: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  const confirm = () => {
    setBusy(true);
    endProgram(p.name).then(
      (r) => {
        setBusy(false);
        setAsking(false);
        toast.success(r.still_running ? `${p.name}: ${r.ended} ended, ${r.still_running} still running` : `Ended ${p.name}`);
        onEnded();
      },
      (e: Error) => {
        setBusy(false);
        setAsking(false);
        toast.error(`Could not end ${p.name}: ${e.message}`);
      },
    );
  };

  return (
    <>
      <tr>
        <td style={{ padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
          {p.name}
          {p.count > 1 ? <span style={{ color: DIM, fontSize: 11 }}> ×{p.count}</span> : null}
          {p.sage ? (
            <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 6, background: 'rgba(34,211,238,0.15)', color: '#67e8f9', marginLeft: 6 }}>Sage</span>
          ) : null}
        </td>
        <td style={{ textAlign: 'right', color: MUTED, fontVariantNumeric: 'tabular-nums' }}>{p.cpu.toFixed(1)}%</td>
        <td style={{ textAlign: 'right', color: MUTED, fontVariantNumeric: 'tabular-nums' }}>{p.mem_gb.toFixed(1)} GB</td>
        <td style={{ textAlign: 'right', width: 56 }}>
          {p.can_end && !asking ? (
            <button
              type="button"
              onClick={() => setAsking(true)}
              aria-label={`End ${p.name}`}
              style={{ fontSize: 11, padding: '2px 8px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.14)', background: 'transparent', color: MUTED, cursor: 'pointer' }}
            >
              End
            </button>
          ) : null}
        </td>
      </tr>
      {asking ? (
        <tr>
          <td colSpan={4} style={{ padding: '6px 0 8px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12.5, background: 'rgba(245,165,36,0.08)', border: `1px solid ${AMBER}55`, borderRadius: 8, padding: '6px 10px' }}>
              <span style={{ flex: 1, minWidth: 180 }}>
                End {p.name}{p.count > 1 ? ` (${p.count} processes)` : ''}? Unsaved work in it may be lost.
              </span>
              <button type="button" disabled={busy} onClick={confirm} style={{ fontSize: 12, padding: '3px 10px', borderRadius: 6, border: 'none', background: AMBER, color: '#111', fontWeight: 600, cursor: 'pointer' }}>
                {busy ? 'Ending…' : 'Confirm'}
              </button>
              <button type="button" disabled={busy} onClick={() => setAsking(false)} style={{ fontSize: 12, padding: '3px 10px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.18)', background: 'transparent', color: '#fafafa', cursor: 'pointer' }}>
                Cancel
              </button>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function Row({
  name,
  percent,
  high,
  history,
  detail,
  focused,
}: {
  name: string;
  percent: number;
  high: boolean;
  history?: number[];
  detail: string;
  focused?: boolean;
}) {
  return (
    <div
      className={focused ? (high ? 'sys-breathe-amber' : 'sys-breathe') : undefined}
      style={{
        display: 'grid',
        gridTemplateColumns: '96px 1fr 170px 160px',
        gap: 14,
        alignItems: 'center',
        padding: '9px 6px',
        borderBottom: '1px solid rgba(255,255,255,0.05)',
        fontSize: 13,
        borderRadius: 8,
      }}
    >
      <span>{name}</span>
      <Bar percent={percent} high={high} />
      {history ? (
        <Constellation points={history} high={high} />
      ) : (
        <span style={{ color: high ? AMBER : DIM, fontVariantNumeric: 'tabular-nums' }}>{Math.round(percent)}%</span>
      )}
      <span style={{ color: DIM, fontVariantNumeric: 'tabular-nums', fontSize: 12.5 }}>{detail}</span>
    </div>
  );
}

export function SystemOverlay({ report, onClose }: { report: SystemReport; onClose: () => void }) {
  const { snap: live, history, error } = useSystemStats(true, report.snapshot, report.focus);
  const snap = live ?? report.snapshot;
  const [, setEnded] = useState(0);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const focus = report.focus;
  const gpu = snap.gpu;
  const disk0 = snap.disks[0];
  const ramHigh = isHigh('ram', snap.ram.percent);
  const cpuHigh = isHigh('cpu', snap.cpu.percent);
  const gpuHigh = !!gpu && (isHigh('gpu', gpu.percent) || gpu.temp_c >= 85);

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80 }} role="presentation">
      <style>{SYSTEM_STYLE}</style>
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(10, 10, 11, 0.62)', backdropFilter: 'blur(2px)' }} />
      <div
        style={{ position: 'absolute', inset: 0, boxSizing: 'border-box', padding: '56px 24px 28px', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'auto' }}
        onClick={(event) => {
          if (isBackdropClick(event)) onClose();
        }}
        role="dialog"
        aria-modal="true"
        aria-label="System status"
      >
        <div
          style={{
            position: 'relative',
            width: 'min(940px, 100%)',
            borderRadius: 18,
            background: '#0f1013',
            border: '1px solid rgba(34,211,238,0.22)',
            boxShadow: '0 24px 70px rgba(0,0,0,0.6), 0 0 40px rgba(34,211,238,0.08)',
            overflow: 'hidden',
            color: '#fafafa',
            margin: 'auto',
          }}
        >
          <ParticleWeb />
          <div aria-hidden style={{ position: 'absolute', inset: 0, borderRadius: 18, pointerEvents: 'none', boxShadow: 'inset 0 0 0 1px rgba(34,211,238,0.22), inset 0 0 50px rgba(34,211,238,0.08), inset 0 -40px 80px rgba(183,148,255,0.05)' }} />
          <div style={{ position: 'relative', padding: '20px 24px 16px' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 17, fontWeight: 600 }}>System status</span>
              <span style={{ fontSize: 12, color: DIM }}>
                {snap.host} · {snap.os} · up {uptimeLabel(snap.uptime_s)}
              </span>
              <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, letterSpacing: '0.14em', color: error ? AMBER : '#4ade80' }}>
                <i style={{ width: 7, height: 7, borderRadius: '50%', background: error ? AMBER : '#4ade80', boxShadow: `0 0 8px ${error ? AMBER : '#4ade80'}` }} />
                {error ? 'PAUSED' : 'LIVE'}
              </span>
            </div>

            {/* Sage's line */}
            {snap.summary ? (
              <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 13.5, background: CARD, borderRadius: 12, padding: '10px 14px', marginTop: 14, border: '1px solid rgba(34,211,238,0.18)' }}>
                <span aria-hidden style={{ width: 18, height: 18, flexShrink: 0, marginTop: 2, borderRadius: '50%', background: 'radial-gradient(circle at 40% 40%, #cffafe, #22d3ee 45%, transparent 70%)', boxShadow: '0 0 12px rgba(34,211,238,0.6)' }} />
                <div>
                  <div style={{ fontSize: 10, letterSpacing: '0.32em', color: ACCENT, textShadow: '0 0 8px rgba(34,211,238,0.3)', marginBottom: 2 }}>SAGE</div>
                  {snap.summary}
                </div>
              </div>
            ) : null}

            {/* Rings */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8, margin: '14px 0 4px' }}>
              <Ring percent={snap.cpu.percent} size={108} high={cpuHigh} focused={focus === 'cpu'} label="CPU" detail={`${snap.cpu.threads} threads · ${snap.cpu.ghz} GHz`} />
              <Ring percent={snap.ram.percent} size={108} high={ramHigh} focused={focus === 'ram'} label="Memory" detail={`${snap.ram.used_gb} / ${snap.ram.total_gb} GB`} />
              <Ring percent={gpu?.percent ?? 0} size={108} high={gpuHigh} focused={focus === 'gpu'} label="GPU" detail={gpu ? `${gpu.vram_used_gb} / ${gpu.vram_total_gb} GB · ${Math.round(gpu.temp_c)}°C` : 'not available'} />
              {disk0 ? (
                <Ring percent={disk0.percent} size={108} high={isHigh('disk', disk0.percent)} focused={focus === 'disk'} label={`Disk ${disk0.name}`} detail={`${disk0.free_gb} GB free`} />
              ) : null}
            </div>

            {/* Rows */}
            <div>
              <Row name="CPU" percent={snap.cpu.percent} high={cpuHigh} history={history.cpu} detail={`${snap.cpu.threads} threads · ${snap.cpu.ghz} GHz`} focused={focus === 'cpu'} />
              <Row name="Memory" percent={snap.ram.percent} high={ramHigh} history={history.ram} detail={`${snap.ram.used_gb} / ${snap.ram.total_gb} GB`} focused={focus === 'ram'} />
              {gpu ? <Row name="GPU" percent={gpu.percent} high={gpuHigh} history={history.gpu} detail={`${gpu.name} · ${Math.round(gpu.temp_c)}°C`} focused={focus === 'gpu'} /> : null}
              {snap.disks.map((d) => (
                <Row key={d.name} name={`Disk ${d.name}`} percent={d.percent} high={isHigh('disk', d.percent)} detail={`${d.free_gb} GB free of ${d.total_gb}`} focused={focus === 'disk'} />
              ))}
            </div>

            {/* Programs + network */}
            <div style={{ display: 'grid', gridTemplateColumns: '1.25fr 1fr', gap: 20, marginTop: 14 }}>
              <div>
                <div style={{ fontSize: 11, letterSpacing: '0.14em', fontWeight: 600, color: MUTED, marginBottom: 6 }}>USING THE MOST MEMORY · SAGE MARKED</div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                  <tbody>
                    {snap.top_by_memory.slice(0, 6).map((p) => (
                      <ProgramRow key={p.name} p={p} onEnded={() => setEnded((n) => n + 1)} />
                    ))}
                  </tbody>
                </table>
                <div style={{ color: DIM, fontSize: 12, marginTop: 6 }}>
                  Sage altogether: <b style={{ color: '#fafafa', fontVariantNumeric: 'tabular-nums' }}>{snap.sage_total.mem_gb} GB · {snap.sage_total.cpu}% CPU</b>
                </div>
              </div>
              <div
                className={focus === 'network' || focus === 'battery' ? 'sys-breathe' : undefined}
                style={{ borderRadius: 10, padding: focus === 'network' || focus === 'battery' ? 8 : 0 }}
              >
                <div style={{ fontSize: 11, letterSpacing: '0.14em', fontWeight: 600, color: MUTED, marginBottom: 6 }}>NETWORK &amp; POWER</div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                  <tbody>
                    {[
                      ['Download', `${snap.net.down_mbps.toFixed(1)} MB/s`],
                      ['Upload', `${snap.net.up_mbps.toFixed(2)} MB/s`],
                      ['Battery', snap.battery ? `${snap.battery.percent}% · ${snap.battery.plugged ? 'plugged in' : 'on battery'}` : 'none'],
                      ['Uptime', uptimeLabel(snap.uptime_s)],
                    ].map(([k, v]) => (
                      <tr key={k}>
                        <td style={{ padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>{k}</td>
                        <td style={{ textAlign: 'right', color: MUTED, fontVariantNumeric: 'tabular-nums', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>{v}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div style={{ fontSize: 11.5, color: DIM, marginTop: 10, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
              <span>Refreshes every second while open · programs every 3 s</span>
              <span>Esc, Close or “close it”</span>
            </div>
          </div>
        </div>
      </div>

      <button
        type="button"
        aria-label="Close system status"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
        style={{ position: 'absolute', top: 18, right: 20, padding: '8px 16px', borderRadius: 999, border: '1px solid rgba(255,255,255,0.18)', background: 'rgba(24,24,27,0.85)', color: '#fafafa', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}
      >
        Close
      </button>
    </div>
  );
}
