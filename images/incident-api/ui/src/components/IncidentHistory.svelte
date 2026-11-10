<script lang="ts">
  import { SvelteMap } from 'svelte/reactivity';
  import { api } from '../lib/api';
  import { clock } from '../lib/clock.svelte';
  import { clockTime, dateTime, duration, horizon } from '../lib/format';
  import { live } from '../lib/live.svelte';
  import type { Transition } from '../lib/types';
  import Badge from './Badge.svelte';
  import EmptyState from './EmptyState.svelte';
  import HorizonMeter from './HorizonMeter.svelte';
  import Icon from './Icon.svelte';
  import SeverityBadge from './SeverityBadge.svelte';

  // One device's incidents (newest first); expanding one loads every
  // transition it went through.
  let { deviceKey }: { deviceKey: string } = $props();

  const POLL_MS = 5000;
  let incidents = $state<Transition[] | null>(null);
  let error = $state<string | null>(null);
  let openId = $state<string | null>(null);
  const timelines = new SvelteMap<string, Transition[] | 'error'>();

  async function load(key: string) {
    try {
      const body = await api.deviceIncidents(key);
      if (key !== deviceKey) return;
      incidents = body.incidents ?? [];
      error = null;
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  // Reload when the device changes, on a timer, and on live changes.
  $effect(() => {
    const key = deviceKey;
    void live.changes;
    void load(key);
    const timer = setInterval(() => void load(key), POLL_MS);
    return () => clearInterval(timer);
  });

  async function toggle(id: string) {
    openId = openId === id ? null : id;
    if (!openId) return;
    timelines.delete(id);
    try {
      timelines.set(id, (await api.transitions(id)).transitions ?? []);
    } catch {
      timelines.set(id, 'error');
    }
  }

  const statusLabel: Record<string, string> = { OPEN: 'Opened', UPDATE: 'Updated', RESOLVE: 'Resolved' };
</script>

{#if incidents === null}
  {#if error}
    <p class="muted">Could not load incidents ({error}).</p>
  {:else}
    <div class="loading" aria-label="Loading incidents">
      <span class="skeleton"></span><span class="skeleton"></span>
    </div>
  {/if}
{:else if incidents.length === 0}
  <EmptyState title="No incidents for this device" testid="history-empty">
    It has not triggered any detection rule so far.
  </EmptyState>
{:else}
  <ol class="list" data-testid="history-rows">
    {#each incidents as t (t.incident_id)}
      {@const resolved = t.status === 'RESOLVE'}
      {@const expanded = openId === t.incident_id}
      {@const end = resolved ? t.detected_at_ms : clock.now}
      <li data-incident={t.incident_id} class:expanded class:resolved>
        <button
          class="head"
          type="button"
          aria-expanded={expanded}
          aria-controls="tl-{t.incident_id}"
          onclick={() => toggle(t.incident_id)}
        >
          <span class="chev"><Icon name="chevron" /></span>
          <span class="main">
            <span class="row1">
              <SeverityBadge severity={t.severity} muted={resolved} />
              {#if resolved}
                <Badge tone="ok">Resolved</Badge>
              {:else}
                <Badge tone="high" dot pulse>Ongoing</Badge>
              {/if}
              <span class="muted when" title={dateTime(t.episode_start_ms)}>{dateTime(t.episode_start_ms)}</span>
            </span>
            <span class="row2 muted">
              <span><Icon name="clock" size={13} />{duration((end ?? 0) - (t.episode_start_ms ?? 0))}</span>
              <span>{t.revision} {t.revision === 1 ? 'change' : 'changes'}</span>
              <span>last change {clockTime(t.detected_at_ms)}</span>
            </span>
          </span>
          <span class="meters">
            <span><small>60 s</small><HorizonMeter h={horizon(t, 60)} /></span>
            <span><small>15 min</small><HorizonMeter h={horizon(t, 900)} /></span>
          </span>
        </button>

        {#if expanded}
          {@const steps = timelines.get(t.incident_id)}
          <div class="timeline" id="tl-{t.incident_id}" data-testid="timeline">
            {#if steps === undefined}
              <span class="skeleton"></span>
            {:else if steps === 'error'}
              <p class="muted">Could not load the transitions.</p>
            {:else}
              <ol>
                {#each steps as s (s.revision)}
                  <li class="step step-{s.status.toLowerCase()}">
                    <span class="marker" aria-hidden="true"></span>
                    <span class="step-time num">{clockTime(s.detected_at_ms)}</span>
                    <span class="step-status" title={s.status}>{statusLabel[s.status] ?? s.status}</span>
                    <SeverityBadge severity={s.severity} />
                    <span class="step-meters">
                      <HorizonMeter h={horizon(s, 60)} />
                      <HorizonMeter h={horizon(s, 900)} />
                    </span>
                  </li>
                {/each}
              </ol>
            {/if}
          </div>
        {/if}
      </li>
    {/each}
  </ol>
{/if}

<style>
  .loading {
    display: grid;
    gap: 10px;
  }
  .loading .skeleton {
    height: 56px;
    border-radius: var(--radius);
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 10px;
  }
  .list > li {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    overflow: hidden;
    transition: border-color 150ms;
  }
  .list > li:not(.resolved) {
    border-left: 3px solid var(--high);
  }
  .list > li.expanded {
    border-color: var(--border-strong);
  }
  .head {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 12px 14px;
    border: 0;
    background: transparent;
    text-align: left;
    cursor: pointer;
  }
  .head:hover {
    background: var(--surface-hover);
  }
  .chev {
    color: var(--faint);
    transition: rotate 160ms var(--ease);
  }
  .expanded .chev {
    rotate: 90deg;
    color: var(--accent);
  }
  .main {
    flex: 1;
    min-width: 0;
    display: grid;
    gap: 4px;
  }
  .row1,
  .row2 {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .row2 {
    font-size: 12.5px;
    gap: 14px;
  }
  .row2 span {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .when {
    font-size: 12.5px;
  }
  .meters {
    display: flex;
    gap: 14px;
  }
  .meters > span {
    display: grid;
    gap: 2px;
  }
  small {
    color: var(--muted);
    font-size: 11px;
  }
  .timeline {
    padding: 4px 16px 14px 40px;
    background: var(--surface-2);
    border-top: 1px solid var(--border);
    animation: open 200ms var(--ease);
  }
  .timeline ol {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .step {
    position: relative;
    display: grid;
    grid-template-columns: 76px 72px auto 1fr;
    align-items: center;
    gap: 10px;
    padding: 10px 0 10px 18px;
  }
  .step::before {
    content: '';
    position: absolute;
    left: 4px;
    top: 0;
    bottom: 0;
    width: 2px;
    background: var(--border-strong);
  }
  .step:first-child::before {
    top: 50%;
  }
  .step:last-child::before {
    bottom: 50%;
  }
  .marker {
    position: absolute;
    left: 0;
    top: 50%;
    width: 10px;
    height: 10px;
    margin-top: -5px;
    border-radius: 50%;
    background: var(--surface);
    border: 2px solid var(--accent);
  }
  .step-open .marker {
    border-color: var(--high);
    background: var(--high);
  }
  .step-resolve .marker {
    border-color: var(--ok);
    background: var(--ok);
  }
  .step-time {
    color: var(--muted);
    font-size: 12.5px;
  }
  .step-status {
    font-weight: 600;
  }
  .step-meters {
    display: flex;
    gap: 12px;
    justify-content: flex-end;
  }
  @keyframes open {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
  }
  @media (max-width: 520px) {
    .meters {
      display: none;
    }
    .step {
      grid-template-columns: 70px 1fr auto;
    }
    .step-meters {
      display: none;
    }
  }
</style>
