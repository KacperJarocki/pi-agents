import { api } from './api';
import type { Device, DevicesResponse } from './types';

// Device overview polled from /api/v2/devices (latest rolling windows,
// incident counts and DHCP leases). Also gives the live page hostnames.

const POLL_MS = 5000;

class DeviceStore {
  devices = $state<Device[]>([]);
  leases = $state<DevicesResponse['leases']>({ available: true, error: null });
  loaded = $state(false);
  error = $state<string | null>(null);
  updatedAt = $state<number | null>(null);

  #timer: ReturnType<typeof setTimeout> | undefined;
  #started = false;

  byKey = $derived(new Map(this.devices.filter((d) => d.device_key).map((d) => [d.device_key as string, d])));

  start() {
    if (this.#started) return;
    this.#started = true;
    void this.#poll();
  }

  /** Reload now (e.g. after a live incident change) and restart the poll timer. */
  refresh() {
    if (!this.#started) return;
    clearTimeout(this.#timer);
    void this.#poll();
  }

  async #poll() {
    try {
      const body = await api.devices();
      this.devices = body.devices ?? [];
      this.leases = body.leases ?? { available: true, error: null };
      this.error = null;
      this.updatedAt = Date.now();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    } finally {
      this.loaded = true;
      clearTimeout(this.#timer);
      this.#timer = setTimeout(() => void this.#poll(), POLL_MS);
    }
  }
}

export const devices = new DeviceStore();
