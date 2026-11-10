import type { DevicesResponse, Transition } from './types';

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const resp = await fetch(url, { headers: { accept: 'application/json' }, signal });
  if (!resp.ok) throw new Error(`${url}: HTTP ${resp.status}`);
  return (await resp.json()) as T;
}

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
};
