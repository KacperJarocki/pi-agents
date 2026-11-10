import type { DevicesResponse, HistoryPoint, Transition } from './types';

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(url, { ...init, headers: { accept: 'application/json', ...init.headers } });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      detail = ((await resp.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

const getJson = <T>(url: string, signal?: AbortSignal) => request<T>(url, { signal });

export const api = {
  devices: (signal?: AbortSignal) => getJson<DevicesResponse>('/api/v2/devices', signal),
  deviceIncidents: (deviceKey: string, signal?: AbortSignal) =>
    getJson<{ cursor: number; incidents: Transition[] }>(
      `/api/v2/incidents?active=false&device=${encodeURIComponent(deviceKey)}`,
      signal,
    ),
  transitions: (incidentId: string, signal?: AbortSignal) =>
    getJson<{ incident_id: string; transitions: Transition[] }>(
      `/api/v2/incidents/${encodeURIComponent(incidentId)}/transitions`,
      signal,
    ),
  history: (deviceKey: string, minutes: number, signal?: AbortSignal) =>
    getJson<{ points: HistoryPoint[] }>(
      `/api/v2/devices/${encodeURIComponent(deviceKey)}/history?minutes=${minutes}`,
      signal,
    ),
  setBlocked: (mac: string, blocked: boolean) =>
    request<{ mac: string; blocked: boolean; message: string }>(`/api/v2/devices/${encodeURIComponent(mac)}/block`, {
      method: blocked ? 'POST' : 'DELETE',
    }),
};
