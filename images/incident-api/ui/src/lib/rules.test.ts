import { describe, expect, it } from 'vitest';
import { explain, ruleInfo } from './rules';

const horizons = (v60: number, v900: number) => [
  { horizon_seconds: 60, value: v60, open_threshold: 20, over_threshold: v60 >= 20 },
  { horizon_seconds: 900, value: v900, open_threshold: 40, over_threshold: v900 >= 40 },
];

describe('rules', () => {
  it('names known rules and falls back to the threat family', () => {
    expect(ruleInfo({ rule_id: 'port-scan' }).title).toBe('Port scan');
    expect(ruleInfo({ rule_id: 'x', threat_family: 'DNS_TUNNEL' }).title).toBe('Dns tunnel');
    expect(ruleInfo({}).title).toBe('Detection');
  });

  it('explains the horizons over threshold', () => {
    expect(explain({ rule_id: 'port-scan', status: 'OPEN', horizons: horizons(241, 30) })).toBe(
      '241 refused or unanswered targets in 60 s (opens at 20)',
    );
    expect(explain({ rule_id: 'port-scan', status: 'UPDATE', horizons: horizons(241, 860) })).toBe(
      '241 refused or unanswered targets in 60 s (opens at 20), 860 in 15 min (opens at 40)',
    );
    expect(explain({ rule_id: 'port-scan', status: 'RESOLVE', horizons: horizons(2, 30) })).toBe(
      'Back down: 2 refused or unanswered targets in 60 s (opens at 20)',
    );
    expect(explain({ status: 'OPEN', horizons: [] })).toBe('');
  });
});
