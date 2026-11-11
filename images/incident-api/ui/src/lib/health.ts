import { clockTime } from './format';
import type { GatewayHealth } from './types';

// A few frames without a DHCP lease are normal (a client before DHCP, a
// randomised MAC); only a burst means attribution is really failing. Count
// problems age out once the sensor stops reporting them.
export const UNATTRIBUTED_MIN_FRAMES = 20;
export const HEALTH_STALE_MS = 15 * 60 * 1000;

/** Human-readable ingestion problems worth a banner; empty when healthy. */
export function ingestionProblems(health: GatewayHealth[], now = Date.now()): string[] {
  const problems: string[] = [];
  const fresh = (g: GatewayHealth) => !g.last_event_time_ms || now - g.last_event_time_ms < HEALTH_STALE_MS;
  const span = (g: GatewayHealth) => `${clockTime(g.first_event_time_ms)}–${clockTime(g.last_event_time_ms)}`;
  for (const g of health) {
    if (g.kind === 'SPOOL_LOSS' && g.count > 0) {
      problems.push(
        `Gateway ${g.gateway_id} lost ${g.count} events (sequences ${g.first_sequence}–${g.last_sequence}, ${span(g)}); detection may be incomplete.`,
      );
    } else if (g.kind === 'UNATTRIBUTED' && g.count >= UNATTRIBUTED_MIN_FRAMES && fresh(g)) {
      problems.push(`Gateway ${g.gateway_id}: ${g.count} frames from devices without a DHCP lease (${span(g)}).`);
    } else if (g.kind === 'FLOW_TABLE_EVICTION' && g.count > 0 && fresh(g)) {
      problems.push(`Gateway ${g.gateway_id}: flow table full, ${g.count} flows flushed early.`);
    } else if (g.kind === 'HEARTBEAT' && g.detail) {
      problems.push(`Gateway ${g.gateway_id}: ${g.detail}.`);
    }
  }
  return problems;
}
