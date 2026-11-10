import { describe, expect, it } from 'vitest';
import { ingestionProblems } from './health';
import type { GatewayHealth } from './types';

const g = (extra: Partial<GatewayHealth>): GatewayHealth => ({
  gateway_id: 'gw',
  component: 'sensor',
  kind: 'HEARTBEAT',
  count: 0,
  first_sequence: 0,
  last_sequence: 0,
  ...extra,
});

describe('ingestionProblems', () => {
  const now = 10_000_000;

  it('reports spool loss and heartbeat details', () => {
    const p = ingestionProblems(
      [
        g({ kind: 'SPOOL_LOSS', count: 42, first_sequence: 100, last_sequence: 141 }),
        g({ detail: 'waiting for wlan0' }),
      ],
      now,
    );
    expect(p).toHaveLength(2);
    expect(p[0]).toContain('lost 42 events');
    expect(p[1]).toContain('waiting for wlan0');
  });

  it('ignores a few unattributed frames and stale count problems', () => {
    expect(
      ingestionProblems(
        [
          g({ kind: 'UNATTRIBUTED', count: 3, last_event_time_ms: now }),
          g({ kind: 'FLOW_TABLE_EVICTION', count: 9, last_event_time_ms: now - 3_600_000 }),
          g({ count: 10 }),
        ],
        now,
      ),
    ).toEqual([]);
  });

  it('reports a burst of unattributed frames', () => {
    expect(ingestionProblems([g({ kind: 'UNATTRIBUTED', count: 250, last_event_time_ms: now })], now)[0]).toContain(
      '250 frames from devices without a DHCP lease',
    );
  });
});
