import { describe, expect, it } from 'vitest';
import { isCloseDiagramCommand } from './diagram';
import { useSystemPresenter } from './system-presenter';
import { isHigh, systemFromToolCall, systemIn, uptimeLabel } from './system-report';

const SNAP = {
  host: 'Grim',
  os: 'Windows 11',
  uptime_s: 156_000,
  cpu: { percent: 20, threads: 16, ghz: 2.4 },
  ram: { percent: 87, used_gb: 13.6, total_gb: 15.6 },
  gpu: null,
  disks: [],
  net: { down_mbps: 0, up_mbps: 0 },
  battery: null,
  top_by_memory: [],
  top_by_cpu: [],
  sage_total: { cpu: 1, mem_gb: 4.5 },
};

const call = (id: string, metadata: Record<string, unknown> | undefined, tool = 'system_status') => ({
  id,
  tool,
  arguments: '{}',
  status: 'success' as const,
  metadata,
});

describe('system report from a tool call', () => {
  it('reads the snapshot and the asked part', () => {
    const r = systemFromToolCall(call('t1', { system: { ...SNAP, focus: 'ram' } }));
    expect(r?.focus).toBe('ram');
    expect(r?.snapshot.host).toBe('Grim');
    expect(r?.key).toBe('t1');
  });

  it('ignores other tools and empty results', () => {
    expect(systemFromToolCall(call('a', { system: SNAP }, 'system_health'))).toBeNull();
    expect(systemFromToolCall(call('b', {}))).toBeNull();
    expect(systemIn([call('c', { system: SNAP })])?.focus).toBeNull();
  });

  it('labels uptime and flags high parts', () => {
    expect(uptimeLabel(156_000)).toBe('1 d 19 h');
    expect(uptimeLabel(3_700)).toBe('1 h 1 min');
    expect(isHigh('ram', 87)).toBe(true);
    expect(isHigh('cpu', 87)).toBe(false);
  });
});

describe('the system panel opens once per answer', () => {
  it('auto-opens a new report, never the same one twice', () => {
    const r = systemFromToolCall(call('once', { system: SNAP }))!;
    expect(useSystemPresenter.getState().showNew(r, true)).toBe(true);
    useSystemPresenter.getState().close();
    expect(useSystemPresenter.getState().showNew(r, true)).toBe(false);
  });
});

describe('closing it by voice', () => {
  it.each(['close the system panel', 'hide the status', 'close diagnostics'])('%s', (said) => {
    expect(isCloseDiagramCommand(said)).toBe(true);
  });

  it('does not catch system questions', () => {
    expect(isCloseDiagramCommand("what's my system status")).toBe(false);
  });
});
