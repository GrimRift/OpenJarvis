/**
 * This PC's load for the system panel (M41), as `system_status` returns it in
 * `metadata.system` and `/v1/system/stats` streams it while the panel is open.
 */

import type { ToolCallInfo } from '../types';

export type SystemPart = 'cpu' | 'ram' | 'gpu' | 'disk' | 'network' | 'battery';

export interface SystemProgram {
  name: string;
  cpu: number;
  mem_gb: number;
  count: number;
  sage: boolean;
  can_end: boolean;
}

export interface SystemSnapshot {
  host: string;
  os: string;
  uptime_s: number;
  cpu: { percent: number; threads: number; ghz: number };
  ram: { percent: number; used_gb: number; total_gb: number };
  gpu: { name: string; percent: number; vram_used_gb: number; vram_total_gb: number; temp_c: number } | null;
  disks: { name: string; percent: number; free_gb: number; total_gb: number }[];
  net: { down_mbps: number; up_mbps: number };
  battery: { percent: number; plugged: boolean } | null;
  top_by_memory: SystemProgram[];
  top_by_cpu: SystemProgram[];
  sage_total: { cpu: number; mem_gb: number };
  summary?: string;
}

export interface SystemReport {
  /** Stable per tool call: what "seen" is keyed by. */
  key: string;
  snapshot: SystemSnapshot;
  /** The part the question was about ("how's my memory?"), if any. */
  focus: SystemPart | null;
}

export const SYSTEM_TOOL = 'system_status';

/** Above these a part shows amber (same thresholds as the server's summary). */
export const HIGH = { cpu: 90, ram: 85, gpu: 90, gpu_temp: 85, disk: 90 };

export function systemFromToolCall(
  call: Pick<ToolCallInfo, 'id' | 'tool' | 'status' | 'metadata'>,
): SystemReport | null {
  if (call.tool !== SYSTEM_TOOL || call.status !== 'success') return null;
  const raw = call.metadata?.system as (SystemSnapshot & { focus?: SystemPart | null }) | undefined;
  if (!raw || typeof raw !== 'object' || !raw.cpu || !raw.ram) return null;
  const { focus, ...snapshot } = raw;
  return { key: call.id || `system:${raw.uptime_s}`, snapshot, focus: focus ?? null };
}

export function systemIn(toolCalls: ToolCallInfo[] | undefined): SystemReport | null {
  let found: SystemReport | null = null;
  for (const call of toolCalls ?? []) found = systemFromToolCall(call) ?? found;
  return found;
}

export function uptimeLabel(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

export function isHigh(part: 'cpu' | 'ram' | 'gpu' | 'disk', percent: number): boolean {
  return percent >= HIGH[part];
}
