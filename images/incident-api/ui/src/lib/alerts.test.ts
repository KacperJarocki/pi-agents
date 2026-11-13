import { describe, expect, it } from 'vitest';
import { alertKind, alertTitle, severityRank } from './alerts';
import type { Transition } from './types';

const t = (over: Partial<Transition>): Transition => ({
  incident_id: 'i1',
  revision: 1,
  status: 'OPEN',
  severity: 'LOW',
  device_key: 'gw:aa:bb:cc:dd:ee:ff',
  rule_id: 'port-scan',
  horizons: [
    { horizon_seconds: 60, value: 20, open_threshold: 20, over_threshold: true },
    { horizon_seconds: 900, value: 20, open_threshold: 40, over_threshold: false },
  ],
  ...over,
});

const quiet = [
  { horizon_seconds: 60, value: 19, open_threshold: 20, over_threshold: false },
  { horizon_seconds: 900, value: 32, open_threshold: 40, over_threshold: false },
];

describe('alertKind', () => {
  it('alerts on a new or reopened incident', () => {
    expect(alertKind(undefined, t({}))).toBe('open');
    expect(alertKind(t({ revision: 3, status: 'RESOLVE' }), t({ revision: 4, status: 'REOPEN' }))).toBe('open');
  });

  it('alerts when severity rises, not when it falls', () => {
    expect(alertKind(t({}), t({ revision: 2, status: 'UPDATE', severity: 'HIGH' }))).toBe('escalate');
    expect(alertKind(t({ severity: 'HIGH' }), t({ revision: 2, status: 'UPDATE', severity: 'LOW' }))).toBeNull();
  });

  it('alerts on a new wave inside an active incident', () => {
    const prev = t({ revision: 2, status: 'UPDATE', horizons: quiet });
    expect(alertKind(prev, t({ revision: 3, status: 'UPDATE' }))).toBe('renewed');
  });

  it('stays quiet for draining updates, resolves and stale revisions', () => {
    expect(alertKind(t({}), t({ revision: 2, status: 'UPDATE', horizons: quiet }))).toBeNull();
    expect(alertKind(t({}), t({ revision: 2, status: 'RESOLVE', horizons: quiet }))).toBeNull();
    expect(alertKind(t({ revision: 5 }), t({ revision: 5 }))).toBeNull();
  });
});

describe('alert text', () => {
  it('ranks severities', () => {
    expect(severityRank('high')).toBeGreaterThan(severityRank('MEDIUM'));
    expect(severityRank(undefined)).toBe(0);
  });

  it('names the change', () => {
    expect(alertTitle('open', 'Port scan', 'low')).toBe('Port scan detected (LOW)');
    expect(alertTitle('escalate', 'Port scan', 'HIGH')).toBe('Port scan escalated to HIGH');
  });
});
