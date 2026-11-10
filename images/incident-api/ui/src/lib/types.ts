// Shapes of the incident-api JSON (images/incident-api/README.md).

export type Severity = 'LOW' | 'MEDIUM' | 'HIGH';
export type TransitionStatus = 'OPEN' | 'UPDATE' | 'RESOLVE';

export interface Horizon {
  horizon_seconds: number;
  value: number;
  open_threshold: number;
  over_threshold: boolean;
}

export interface Transition {
  incident_id: string;
  transition_id?: string;
  revision: number;
  status: TransitionStatus | string;
  severity: Severity | string;
  device_key: string;
  horizons?: Horizon[];
  episode_start_ms?: number | null;
  evidence_event_time_ms?: number | null;
  detected_at_ms?: number | null;
  committed_at_ms?: number | null;
}

export interface GatewayHealth {
  gateway_id: string;
  component: string;
  kind: 'SPOOL_LOSS' | 'UNATTRIBUTED' | 'FLOW_TABLE_EVICTION' | 'HEARTBEAT' | string;
  count: number;
  first_sequence: number;
  last_sequence: number;
  detail?: string | null;
  first_event_time_ms?: number | null;
  last_event_time_ms?: number | null;
}

export interface WindowStats {
  flows?: number | null;
  distinct_targets?: number | null;
  failed_targets?: number | null;
  bytes?: number | null;
  window_end_ms?: number | null;
}

export interface Device {
  mac: string;
  device_key: string | null;
  gateway_id?: string | null;
  hostname: string | null;
  ip: string | null;
  lease_expires_at_ms?: number | null;
  in_lease_table?: boolean;
  last_seen_ms: number | null;
  features: Partial<Record<'60' | '900', WindowStats>>;
  incidents: { active?: number; total?: number; last_detected_ms?: number | null; severity?: Severity | null };
}

export interface DevicesResponse {
  devices: Device[];
  leases?: { available: boolean; error: string | null };
  generated_at_ms?: number;
}

export type FeedMessage =
  | { type: 'snapshot'; cursor: number; incidents: Transition[] }
  | { type: 'reset'; reason?: string }
  | { type: 'change'; cursor: number; transition: Transition; committed_at_ms?: number }
  | { type: 'health'; gateways: GatewayHealth[] }
  | { type: 'live' | 'ping'; cursor: number };
