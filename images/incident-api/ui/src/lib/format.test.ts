import { describe, expect, it } from 'vitest';
import { ago, bytes, delay, delayMs, duration, horizon, titleCase, until } from './format';

describe('format', () => {
  const now = 1_000_000_000;

  it('formats relative times', () => {
    expect(ago(null, now)).toBe('never');
    expect(ago(now - 2_000, now)).toBe('just now');
    expect(ago(now - 30_000, now)).toBe('30 s ago');
    expect(ago(now - 180_000, now)).toBe('3 min ago');
    expect(ago(now - 7_200_000, now)).toBe('2 h ago');
    expect(until(now + 600_000, now)).toBe('in 10 min');
  });

  it('formats durations, bytes and delays', () => {
    expect(duration(45_000)).toBe('45 s');
    expect(duration(125_000)).toBe('2 min 5 s');
    expect(duration(-1)).toBe('–');
    expect(bytes(512)).toBe('512 B');
    expect(bytes(2_500_000)).toBe('2.4 MB');
    expect(delay(delayMs({ committed_at_ms: 1_500, evidence_event_time_ms: 1_000 }))).toBe('500 ms');
    expect(delay(12_345)).toBe('12.3 s');
    expect(delay(-99_743)).toBe('clock skew');
    expect(delay(delayMs({ committed_at_ms: null, evidence_event_time_ms: 1 }))).toBe('–');
  });

  it('finds a horizon and title-cases severities', () => {
    const t = { horizons: [{ horizon_seconds: 60, value: 1, open_threshold: 2, over_threshold: false }] };
    expect(horizon(t, 60)?.value).toBe(1);
    expect(horizon(t, 900)).toBeUndefined();
    expect(titleCase('MEDIUM')).toBe('Medium');
  });
});
