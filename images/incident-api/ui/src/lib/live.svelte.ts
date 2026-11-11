import { SvelteMap } from 'svelte/reactivity';
import type { FeedMessage, GatewayHealth, Transition } from './types';

// Live incidents over /ws/v2/incidents. One entry per incident; a transition
// is applied only if its revision is newer. The last cursor is kept so a
// reconnect resumes with ?after=<cursor> instead of a full reload.

export interface LiveIncident extends Transition {
  /** Local time the row last changed through a `change` message (for highlighting). */
  changed_at?: number;
}

export type Connection = 'connecting' | 'live' | 'offline';

const MIN_RETRY_MS = 1000;
const MAX_RETRY_MS = 30000;

class LiveFeed {
  incidents = new SvelteMap<string, LiveIncident>();
  health = $state<GatewayHealth[]>([]);
  cursor = $state<number | null>(null);
  connection = $state<Connection>('connecting');
  /** When the next reconnect attempt happens (while offline). */
  retryAt = $state<number | null>(null);
  /** Bumped on every applied change; lets other views refresh. */
  changes = $state(0);

  #retryMs = MIN_RETRY_MS;
  #timer: ReturnType<typeof setTimeout> | undefined;
  #socket: WebSocket | null = null;
  #started = false;

  get active(): LiveIncident[] {
    return [...this.incidents.values()].filter((t) => t.status !== 'RESOLVE');
  }

  start() {
    if (this.#started) return;
    this.#started = true;
    this.#connect();
  }

  reconnectNow() {
    clearTimeout(this.#timer);
    this.#retryMs = MIN_RETRY_MS;
    this.#connect();
  }

  #apply(transition: Transition, committedAtMs: number | null | undefined, changed: boolean) {
    const current = this.incidents.get(transition.incident_id);
    if (current && current.revision >= transition.revision) return false;
    this.incidents.set(transition.incident_id, {
      ...transition,
      committed_at_ms: committedAtMs ?? transition.committed_at_ms,
      changed_at: changed ? Date.now() : undefined,
    });
    return true;
  }

  #handle(msg: FeedMessage) {
    switch (msg.type) {
      case 'snapshot':
        this.incidents.clear();
        for (const t of msg.incidents) this.#apply(t, t.committed_at_ms, false);
        this.cursor = msg.cursor;
        break;
      case 'reset':
        this.incidents.clear();
        break;
      case 'change':
        if (this.#apply(msg.transition, msg.committed_at_ms, true)) this.changes++;
        this.cursor = msg.cursor;
        break;
      case 'health':
        this.health = msg.gateways ?? [];
        break;
      case 'live':
      case 'ping':
        this.cursor = msg.cursor;
        this.#retryMs = MIN_RETRY_MS;
        this.connection = 'live';
        this.retryAt = null;
        break;
    }
  }

  #connect() {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const query = this.cursor === null ? '' : `?after=${this.cursor}`;
    if (this.connection !== 'offline') this.connection = 'connecting';
    const socket = new WebSocket(`${scheme}://${location.host}/ws/v2/incidents${query}`);
    this.#socket = socket;
    socket.onmessage = (event) => {
      try {
        this.#handle(JSON.parse(event.data) as FeedMessage);
      } catch (err) {
        console.warn('ignoring malformed feed message', err);
      }
    };
    socket.onclose = () => {
      if (this.#socket !== socket) return;
      const wait = this.#retryMs;
      this.#retryMs = Math.min(this.#retryMs * 2, MAX_RETRY_MS);
      this.connection = 'offline';
      this.retryAt = Date.now() + wait;
      this.#timer = setTimeout(() => this.#connect(), wait);
    };
  }
}

export const live = new LiveFeed();
