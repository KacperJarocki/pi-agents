import { duration, titleCase } from './format';
import type { Horizon, Transition } from './types';

// What each detection rule means, in plain words. Values mirror the Flink
// job's rule configuration (flink/detection-job PortScanRule v1).

export interface RuleInfo {
  /** Short threat name, e.g. "Port scan". */
  title: string;
  /** What the measured value counts. */
  signal: string;
  /** When severity rises. */
  escalation: string;
  /** When the incident resolves. */
  resolution: string;
  /** What a person should do about it. */
  advice: string;
}

const RULES: Record<string, RuleInfo> = {
  'port-scan': {
    title: 'Port scan',
    signal: 'distinct hosts or ports that refused the connection or never answered',
    escalation: 'Medium at twice a threshold, high at four times.',
    resolution: 'Resolves once both windows stay at or below half their threshold for 60 s.',
    advice:
      'A device probing many hosts or ports is often compromised or misconfigured. Check what runs on it, and block it if it should not be scanning your network.',
  },
};

const windowName = (seconds: number) => (seconds % 60 === 0 && seconds >= 120 ? `${seconds / 60} min` : `${seconds} s`);

export function ruleInfo(t: Pick<Transition, 'rule_id' | 'threat_family'>): RuleInfo {
  const known = t.rule_id ? RULES[t.rule_id] : undefined;
  if (known) return known;
  const family = t.threat_family && t.threat_family !== 'UNSPECIFIED' ? t.threat_family : '';
  return {
    title: family ? titleCase(family.replaceAll('_', ' ')) : 'Detection',
    signal: 'the rule signal',
    escalation: 'Severity rises with how far the signal is above its threshold.',
    resolution: 'Resolves once the signal falls back below its threshold.',
    advice: 'Check what the device is doing and whether that traffic is expected.',
  };
}

const NOUNS: Record<string, string> = { 'port-scan': 'refused or unanswered targets' };

/**
 * One line on why the incident exists, e.g.
 * "241 refused or unanswered targets in 60 s (opens at 20)". A resolved
 * incident's last transition only holds the values it settled at, so it says
 * how long the episode lasted instead.
 */
export function explain(
  t: Pick<Transition, 'rule_id' | 'horizons' | 'status' | 'episode_start_ms' | 'detected_at_ms'>,
): string {
  if (t.status === 'RESOLVE') {
    const lasted = t.episode_start_ms && t.detected_at_ms ? duration(t.detected_at_ms - t.episode_start_ms) : '';
    return lasted ? `Resolved after ${lasted}` : 'Resolved';
  }
  const all = t.horizons ?? [];
  const over = all.filter((h) => h.over_threshold);
  const shown: Horizon[] = over.length ? over : all.slice(0, 1);
  if (!shown.length) return '';
  const noun = (t.rule_id && NOUNS[t.rule_id]) || 'signal';
  const part = (h: Horizon) =>
    `${Math.round(h.value)} in ${windowName(h.horizon_seconds)} (opens at ${h.open_threshold})`;
  const [first, ...rest] = shown.map(part);
  const head = first!.replace(' in ', ` ${noun} in `);
  return [head, ...rest].join(', ');
}
