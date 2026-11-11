import type { Horizon, Transition } from './types';

export const DASH = '–';

export function clockTime(ms: number | null | undefined): string {
  return ms ? new Date(ms).toLocaleTimeString() : DASH;
}

export function dateTime(ms: number | null | undefined): string {
  return ms ? new Date(ms).toLocaleString() : DASH;
}

/** "5 s ago", "3 min ago", "2 h ago", then the full date. */
export function ago(ms: number | null | undefined, now = Date.now()): string {
  if (!ms) return 'never';
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return dateTime(ms);
}

/** "in 12 min" for a time in the future; falls back to ago(). */
export function until(ms: number | null | undefined, now = Date.now()): string {
  if (!ms) return DASH;
  const s = Math.round((ms - now) / 1000);
  if (s <= 0) return ago(ms, now);
  if (s < 60) return `in ${s} s`;
  if (s < 3600) return `in ${Math.round(s / 60)} min`;
  if (s < 86400) return `in ${Math.round(s / 3600)} h`;
  return dateTime(ms);
}

export function duration(ms: number | null | undefined): string {
  if (ms == null || ms < 0) return DASH;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}

export function bytes(n: number | null | undefined): string {
  if (n == null) return DASH;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n < 10 && i ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

export function count(v: number | null | undefined): string {
  return v == null ? DASH : Math.round(v).toLocaleString();
}

/** Evidence-to-browser delay of a transition, in ms (null when unknown). */
export function delayMs(t: Pick<Transition, 'committed_at_ms' | 'evidence_event_time_ms'>): number | null {
  if (!t.committed_at_ms || !t.evidence_event_time_ms) return null;
  return t.committed_at_ms - t.evidence_event_time_ms;
}

/**
 * A negative delay means the gateway's clock ran ahead of the database's
 * (e.g. a Pi without an RTC before NTP sync): not a real latency.
 */
export function delay(ms: number | null): string {
  if (ms == null) return DASH;
  if (ms < 0) return 'clock skew';
  return ms < 10000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function horizon(t: Pick<Transition, 'horizons'>, seconds: number): Horizon | undefined {
  return (t.horizons ?? []).find((h) => h.horizon_seconds === seconds);
}

export function titleCase(s: string | null | undefined): string {
  if (!s) return '';
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}
