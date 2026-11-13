import { alertKind, alertTitle, type AlertKind } from './alerts';
import { devices } from './devices.svelte';
import { prefs } from './prefs.svelte';
import { ruleInfo } from './rules';
import type { Transition } from './types';

// Turns live incident changes into something a person notices: an in-page
// toast, a short tone and, when the browser allows it, a desktop notification
// (the Notification API needs HTTPS and a user gesture to ask for permission).

export interface Toast {
  id: number;
  kind: AlertKind;
  title: string;
  device: string;
  severity: string;
  incidentId: string;
  at: number;
}

const TOAST_MS = 20_000;
const MAX_TOASTS = 4;

type Permission = NotificationPermission | 'unsupported';

const notificationApi = (): typeof Notification | null =>
  typeof window !== 'undefined' && 'Notification' in window ? window.Notification : null;

class AlertCenter {
  toasts = $state<Toast[]>([]);
  permission = $state<Permission>(notificationApi()?.permission ?? 'unsupported');

  #seq = 0;
  #audio: AudioContext | null = null;

  /** Called by the live feed for every applied `change` message. */
  onChange(prev: Transition | undefined, next: Transition) {
    const kind = alertKind(prev, next);
    if (!kind || !prefs.alerts) return;
    const device = this.#deviceName(next.device_key);
    const title = alertTitle(kind, ruleInfo(next).title, String(next.severity));
    const toast: Toast = {
      id: ++this.#seq,
      kind,
      title,
      device,
      severity: String(next.severity).toUpperCase(),
      incidentId: next.incident_id,
      at: Date.now(),
    };
    this.toasts = [toast, ...this.toasts].slice(0, MAX_TOASTS);
    setTimeout(() => this.dismiss(toast.id), TOAST_MS);
    this.#beep(toast.severity);
    this.#notify(toast);
  }

  dismiss(id: number) {
    this.toasts = this.toasts.filter((t) => t.id !== id);
  }

  /**
   * Bell button. While alerts are on but the browser has not been asked yet,
   * a click asks for desktop notifications; otherwise it toggles alerts.
   */
  async toggle() {
    if (prefs.alerts && this.permission === 'default') {
      await this.requestPermission();
      return;
    }
    prefs.alerts = !prefs.alerts;
    if (prefs.alerts) await this.requestPermission();
  }

  async requestPermission() {
    const api = notificationApi();
    if (!api) return;
    if (api.permission === 'default') {
      try {
        this.permission = await api.requestPermission();
      } catch {
        this.permission = api.permission;
      }
    } else {
      this.permission = api.permission;
    }
    // Browsers only unlock audio after a gesture; this click is one.
    this.#ctx();
  }

  #deviceName(key: string): string {
    const d = devices.byKey.get(key);
    const mac = key.includes(':') ? key.slice(key.indexOf(':') + 1) : key;
    return d?.hostname || d?.ip || mac;
  }

  #ctx(): AudioContext | null {
    if (this.#audio) return this.#audio;
    const Ctx = typeof window !== 'undefined' ? window.AudioContext : undefined;
    if (!Ctx) return null;
    try {
      this.#audio = new Ctx();
    } catch {
      this.#audio = null;
    }
    return this.#audio;
  }

  #beep(severity: string) {
    if (!prefs.alertSound) return;
    const ctx = this.#ctx();
    if (!ctx || ctx.state === 'suspended') return;
    const tones = severity === 'HIGH' ? [880, 660, 880] : severity === 'MEDIUM' ? [740, 740] : [660];
    tones.forEach((freq, i) => {
      const start = ctx.currentTime + i * 0.18;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.15, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.15);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.16);
    });
  }

  #notify(toast: Toast) {
    const api = notificationApi();
    if (!api || api.permission !== 'granted') return;
    try {
      const n = new api(toast.title, {
        body: `Device ${toast.device}`,
        tag: `${toast.incidentId}:${toast.kind}`,
        requireInteraction: toast.severity === 'HIGH',
      });
      n.onclick = () => {
        window.focus();
        if (location.pathname !== '/live/') location.assign('/live/');
        n.close();
      };
    } catch (err) {
      console.warn('desktop notification failed', err);
    }
  }
}

export const alerts = new AlertCenter();
