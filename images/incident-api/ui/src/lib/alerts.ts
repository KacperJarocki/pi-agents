import type { Transition } from './types';

// Which live incident changes deserve an alert. Only `change` messages are
// considered (a snapshot replays history and never alerts).
//
// - open:     the rule opened a new incident or reopened one in its cooldown.
// - escalate: an active incident's severity went up.
// - renewed:  an active incident had no window over threshold and now has one
//             again, i.e. a new attack wave inside the same episode. The rule
//             keeps an incident active while the 900 s window drains, so a
//             second scan minutes later is only an UPDATE, not a new OPEN.

export type AlertKind = 'open' | 'escalate' | 'renewed';

const SEVERITY_RANK: Record<string, number> = { LOW: 1, MEDIUM: 2, HIGH: 3 };

export const severityRank = (s: string | null | undefined): number => SEVERITY_RANK[String(s ?? '').toUpperCase()] ?? 0;

const anyOver = (t: Transition | undefined): boolean => Boolean(t?.horizons?.some((h) => h.over_threshold));

export function alertKind(prev: Transition | undefined, next: Transition): AlertKind | null {
  if (prev && prev.revision >= next.revision) return null;
  const status = String(next.status).toUpperCase();
  if (status === 'OPEN' || status === 'REOPEN') return 'open';
  if (status !== 'UPDATE') return null;
  if (prev && severityRank(next.severity) > severityRank(prev.severity)) return 'escalate';
  if (prev && !anyOver(prev) && anyOver(next)) return 'renewed';
  return null;
}

export function alertTitle(kind: AlertKind, ruleTitle: string, severity: string): string {
  const sev = String(severity).toUpperCase();
  switch (kind) {
    case 'open':
      return `${ruleTitle} detected (${sev})`;
    case 'escalate':
      return `${ruleTitle} escalated to ${sev}`;
    case 'renewed':
      return `${ruleTitle}: new activity (${sev})`;
  }
}
