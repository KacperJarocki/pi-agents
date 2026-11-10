import { describe, expect, it } from 'vitest';
import { deviceState, matches } from './device-state';
import type { Device } from './types';

const now = 10_000_000;
const device = (extra: Partial<Device> = {}): Device => ({
  mac: '02:00:00:00:00:0a',
  device_key: 'gw:02:00:00:00:00:0a',
  hostname: 'zero',
  ip: '192.168.50.134',
  last_seen_ms: now - 5_000,
  features: {},
  incidents: { active: 0, total: 0 },
  ...extra,
});

describe('deviceState', () => {
  it('derives alert, clean, idle and unseen', () => {
    expect(deviceState(device({ incidents: { active: 1, severity: 'HIGH' } }), now)).toMatchObject({
      kind: 'alert',
      tone: 'high',
      label: 'Alert · High',
    });
    expect(deviceState(device(), now).label).toBe('Clean');
    expect(deviceState(device({ last_seen_ms: now - 3_600_000 }), now).label).toBe('Idle');
    expect(deviceState(device({ last_seen_ms: null }), now).label).toBe('No traffic');
  });

  it('matches name, IP and MAC case-insensitively', () => {
    expect(matches(device(), 'ZER')).toBe(true);
    expect(matches(device(), '50.134')).toBe(true);
    expect(matches(device(), '00:0a')).toBe(true);
    expect(matches(device(), 'printer')).toBe(false);
    expect(matches(device(), '  ')).toBe(true);
  });
});
