<script lang="ts">
  import { clock } from '../lib/clock.svelte';
  import { deviceName, deviceState } from '../lib/device-state';
  import { devices } from '../lib/devices.svelte';
  import { ago, bytes, count, dateTime, until } from '../lib/format';
  import { navigate } from '../lib/router.svelte';
  import type { WindowStats } from '../lib/types';
  import Badge from './Badge.svelte';
  import Icon from './Icon.svelte';
  import BlockControl from './BlockControl.svelte';
  import IncidentHistory from './IncidentHistory.svelte';
  import TrafficChart from './TrafficChart.svelte';
  import { horizon } from '../lib/format';
  import type { Transition } from '../lib/types';

  // Slide-over with one device's details and incident history. A modal
  // <dialog> gives focus trapping, Esc to close and an inert background.
  let { deviceKey }: { deviceKey: string } = $props();

  let dialog: HTMLDialogElement;
  let incidents = $state<Transition[] | null>(null);
  const threshold = $derived((incidents ?? []).map((t) => horizon(t, 60)?.open_threshold).find((v) => v != null) ?? 20);
  const device = $derived(devices.byKey.get(deviceKey));
  const st = $derived(device ? deviceState(device, clock.now) : null);

  $effect(() => {
    if (!dialog.open) dialog.showModal();
  });

  const close = () => navigate('/');
  const onBackdrop = (e: MouseEvent) => {
    if (e.target === dialog) close();
  };
</script>

{#snippet windowCard(title: string, w: WindowStats | undefined)}
  <div class="window">
    <h4>{title}</h4>
    <dl>
      <dt>Flows</dt>
      <dd class="num">{count(w?.flows)}</dd>
      <dt>Targets</dt>
      <dd class="num">{count(w?.distinct_targets)}</dd>
      <dt>Failed</dt>
      <dd class="num" class:warn={(w?.failed_targets ?? 0) > 0}>{count(w?.failed_targets)}</dd>
      <dt>Traffic</dt>
      <dd class="num">{bytes(w?.bytes)}</dd>
    </dl>
  </div>
{/snippet}

<dialog
  bind:this={dialog}
  class="drawer"
  data-testid="history"
  aria-labelledby="drawer-title"
  onclose={close}
  onclick={onBackdrop}
>
  <div class="panel">
    <header>
      <span class="avatar"><Icon name="router" size={20} /></span>
      <div class="title">
        <h2 id="drawer-title" data-testid="history-title">{device ? deviceName(device) : deviceKey}</h2>
        <span class="mono muted">{device?.mac ?? deviceKey}</span>
      </div>
      <button class="close" type="button" onclick={close} aria-label="Close"><Icon name="x" size={18} /></button>
    </header>

    <div class="content">
      {#if st}
        <div class="state-line">
          <Badge tone={st.tone} dot pulse={st.kind === 'alert'}>{st.label}</Badge>
          {#if device?.blocked}<Badge tone="high" solid testid="blocked-badge">Blocked</Badge>{/if}
          <span class="muted">last seen {ago(device?.last_seen_ms, clock.now)}</span>
        </div>
      {/if}

      <dl class="facts">
        <dt>IP address</dt>
        <dd class="mono">{device?.ip || '–'}</dd>
        <dt>Gateway</dt>
        <dd>{device?.gateway_id || '–'}</dd>
        <dt>Last seen</dt>
        <dd>{dateTime(device?.last_seen_ms)}</dd>
        <dt>DHCP lease</dt>
        <dd>
          {#if device?.lease_expires_at_ms}expires {until(device.lease_expires_at_ms, clock.now)}{:else}–{/if}
        </dd>
      </dl>

      <div class="windows">
        {@render windowCard('Last 60 s', device?.features['60'])}
        {@render windowCard('Last 15 min', device?.features['900'])}
      </div>

      <TrafficChart {deviceKey} incidents={incidents ?? []} {threshold} />

      <h3>Incident history</h3>
      <IncidentHistory {deviceKey} bind:incidents />

      {#if device}<BlockControl {device} />{/if}
    </div>
  </div>
</dialog>

<style>
  .drawer {
    position: fixed;
    inset: 0 0 0 auto;
    margin: 0;
    padding: 0;
    width: min(620px, 100vw);
    max-width: 100vw;
    height: 100dvh;
    max-height: 100dvh;
    border: 0;
    border-left: 1px solid var(--border);
    background: var(--surface);
    color: var(--text);
    box-shadow: var(--shadow-lg);
    transition:
      translate 260ms var(--ease),
      display 260ms allow-discrete,
      overlay 260ms allow-discrete;
    translate: 0 0;
  }
  @starting-style {
    .drawer[open] {
      translate: 100% 0;
    }
  }
  .drawer::backdrop {
    background: oklch(20% 0.02 264 / 0.35);
    backdrop-filter: blur(2px);
    transition: opacity 260ms;
  }
  @starting-style {
    .drawer[open]::backdrop {
      opacity: 0;
    }
  }
  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
  }
  header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 18px 20px;
    border-bottom: 1px solid var(--border);
  }
  .avatar {
    display: grid;
    place-items: center;
    width: 40px;
    height: 40px;
    border-radius: 12px;
    color: var(--accent);
    background: color-mix(in oklch, var(--accent) 12%, transparent);
  }
  .title {
    flex: 1;
    min-width: 0;
    display: grid;
  }
  h2 {
    font-size: 18px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .close {
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border: 0;
    border-radius: 9px;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
  }
  .close:hover {
    background: var(--surface-2);
    color: var(--text);
  }
  .content {
    flex: 1;
    overflow-y: auto;
    padding: 18px 20px 32px;
  }
  .state-line {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 16px;
  }
  .facts {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 8px 20px;
    margin: 0 0 18px;
  }
  dt {
    color: var(--muted);
  }
  dd {
    margin: 0;
  }
  .windows {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 24px;
  }
  .window {
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface-2);
  }
  h4 {
    margin: 0 0 8px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--muted);
  }
  .window dl {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 4px 12px;
    margin: 0;
  }
  .window dd {
    text-align: right;
    font-weight: 600;
  }
  .warn {
    color: var(--medium);
  }
  h3 {
    font-size: 15px;
    margin-bottom: 10px;
  }
  .content > :global(ol) {
    margin-bottom: 24px;
  }
  @media (max-width: 520px) {
    .windows {
      grid-template-columns: 1fr;
    }
  }
</style>
