import { titleCase } from './format';
import type { Device } from './types';

export const IDLE_MS = 120_000;

export type DeviceStateKind = 'alert' | 'clean' | 'idle' | 'unseen';

export interface DeviceState {
  kind: DeviceStateKind;
  tone: 'high' | 'medium' | 'low' | 'ok' | 'idle';
  label: string;
}

export function deviceState(d: Device, now = Date.now()): DeviceState {
  if ((d.incidents.active ?? 0) > 0) {
    const sev = d.incidents.severity ?? 'LOW';
    return { kind: 'alert', tone: sev.toLowerCase() as DeviceState['tone'], label: `Alert · ${titleCase(sev)}` };
  }
  if (!d.last_seen_ms) return { kind: 'unseen', tone: 'idle', label: 'No traffic' };
  if (now - d.last_seen_ms > IDLE_MS) return { kind: 'idle', tone: 'idle', label: 'Idle' };
  return { kind: 'clean', tone: 'ok', label: 'Clean' };
}

export function deviceName(d: Pick<Device, 'hostname'> | undefined): string {
  return d?.hostname || 'Unknown device';
}

export function matches(d: Device, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [d.hostname, d.ip, d.mac, d.device_key].some((v) => v?.toLowerCase().includes(q));
}
