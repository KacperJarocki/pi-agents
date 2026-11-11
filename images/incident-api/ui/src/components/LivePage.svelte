<script lang="ts">
  import { clock } from '../lib/clock.svelte';
  import { deviceName } from '../lib/device-state';
  import { devices } from '../lib/devices.svelte';
  import { ago, dateTime, delay, delayMs, horizon } from '../lib/format';
  import { ingestionProblems } from '../lib/health';
  import { explain, ruleInfo } from '../lib/rules';
  import { live, type LiveIncident } from '../lib/live.svelte';
  import { prefs } from '../lib/prefs.svelte';
  import { deviceHref, link } from '../lib/router.svelte';
  import Badge from './Badge.svelte';
  import Banner from './Banner.svelte';
  import EmptyState from './EmptyState.svelte';
  import FilterChips from './FilterChips.svelte';
  import HorizonMeter from './HorizonMeter.svelte';
  import SeverityBadge from './SeverityBadge.svelte';
  import StatTile from './StatTile.svelte';

  type SeverityFilter = 'ALL' | 'HIGH' | 'MEDIUM' | 'LOW';
  const FLASH_MS = 2500;

  let severity = $state<SeverityFilter>('ALL');

  const all = $derived(
    [...live.incidents.values()].sort((a, b) => {
      const ra = a.status === 'RESOLVE' ? 1 : 0;
      const rb = b.status === 'RESOLVE' ? 1 : 0;
      return ra - rb || (b.detected_at_ms ?? 0) - (a.detected_at_ms ?? 0);
    }),
  );
  const shown = $derived(all.filter((t) => prefs.showResolved || t.status !== 'RESOLVE'));
  const rows = $derived(shown.filter((t) => severity === 'ALL' || t.severity === severity));
  const bySeverity = (s: string) => shown.filter((t) => t.severity === s).length;

  const active = $derived(live.active);
  const high = $derived(active.filter((t) => t.severity === 'HIGH').length);
  const affected = $derived(new Set(active.map((t) => t.device_key)).size);
  const medianDelay = $derived.by(() => {
    const ds = all
      .map(delayMs)
      .filter((d): d is number => d != null)
      .sort((a, b) => a - b);
    return ds.length ? ds[Math.floor(ds.length / 2)]! : null;
  });

  const problems = $derived(ingestionProblems(live.health, clock.now));
  const retryIn = $derived(live.retryAt ? Math.max(0, Math.ceil((live.retryAt - clock.now) / 1000)) : 0);
  const waiting = $derived(live.cursor === null && live.connection !== 'live');

  const statusBadge = (t: LiveIncident) =>
    t.status === 'RESOLVE'
      ? { tone: 'ok' as const, text: 'Resolved' }
      : t.status === 'OPEN'
        ? { tone: 'high' as const, text: 'Open' }
        : { tone: 'medium' as const, text: 'Ongoing' };
</script>

<div class="page-head">
  <div>
    <h1>Live incidents</h1>
    <p>Detections from the streaming job, pushed the moment they open, change or resolve.</p>
  </div>
</div>

{#if live.connection === 'offline'}
  <Banner
    tone="danger"
    role="alert"
    icon="offline"
    title="Live connection lost — reconnecting in {retryIn} s."
    testid="connection-banner"
  >
    Incidents shown may be out of date.
    {#snippet actions()}
      <button class="btn" type="button" onclick={() => live.reconnectNow()}>Reconnect now</button>
    {/snippet}
  </Banner>
{/if}

{#if problems.length}
  <Banner tone="warn" title="Ingestion degraded" testid="ingestion-banner">
    <ul>
      {#each problems as p (p)}<li>{p}</li>{/each}
    </ul>
  </Banner>
{/if}

<section class="tiles" aria-label="Summary">
  <StatTile
    icon={active.length ? 'alert' : 'shield'}
    label="Open incidents"
    value={active.length}
    hint={active.length ? `on ${affected} ${affected === 1 ? 'device' : 'devices'}` : 'all quiet'}
    tone={active.length ? 'high' : 'ok'}
    testid="open-count"
  />
  <StatTile icon="alert" label="High severity" value={high} hint="open right now" />
  <StatTile icon="activity" label="Resolved" value={all.length - active.length} hint="since the snapshot" />
  <StatTile
    icon="gauge"
    label="Detection delay"
    value={delay(medianDelay)}
    hint="median, evidence → database"
    tone="accent"
  />
</section>

<section class="card">
  <div class="toolbar">
    <FilterChips
      label="Severity"
      bind:value={severity}
      options={[
        { value: 'ALL', label: 'All', count: shown.length },
        { value: 'HIGH', label: 'High', count: bySeverity('HIGH') },
        { value: 'MEDIUM', label: 'Medium', count: bySeverity('MEDIUM') },
        { value: 'LOW', label: 'Low', count: bySeverity('LOW') },
      ]}
    />
    <span class="spacer"></span>
    <label class="switch">
      <input type="checkbox" bind:checked={prefs.showResolved} data-testid="show-resolved" />
      Show resolved
    </label>
  </div>

  <div class="table-wrap">
    <table class="data">
      <thead>
        <tr>
          <th>Severity</th>
          <th>Threat</th>
          <th>Device</th>
          <th>Status</th>
          <th title="Value / open threshold over the 60 s window">60 s</th>
          <th title="Value / open threshold over the 15 min window">15 min</th>
          <th class="hide-sm hide-md">Started</th>
          <th>Updated</th>
          <th class="right hide-sm" title="Evidence event time → committed to the database">Delay</th>
          <th class="right hide-sm" title="Revision">Rev</th>
        </tr>
      </thead>
      <tbody data-testid="incidents">
        {#each rows as t (t.incident_id)}
          {@const d = devices.byKey.get(t.device_key)}
          {@const s = statusBadge(t)}
          <tr
            data-incident={t.incident_id}
            class:resolved={t.status === 'RESOLVE'}
            class:flash={t.changed_at && clock.now - t.changed_at < FLASH_MS}
          >
            <td><SeverityBadge severity={t.severity} muted={t.status === 'RESOLVE'} /></td>
            <td class="threat">
              <strong data-testid="threat">{ruleInfo(t).title}</strong>
              <div class="why" data-testid="why" title={explain(t)}>{explain(t)}</div>
            </td>
            <td>
              <a class="device" href={deviceHref(t.device_key)} onclick={link} title={t.device_key} data-testid="device"
                >{d ? deviceName(d) : t.device_key}</a
              >
              {#if d && (d.ip || d.mac)}
                <div class="mono muted">{[d.ip, d.mac].filter(Boolean).join(' · ')}</div>
              {/if}
            </td>
            <td><Badge tone={s.tone} dot pulse={t.status === 'OPEN'} testid="incident-status">{s.text}</Badge></td>
            <td><HorizonMeter h={horizon(t, 60)} /></td>
            <td><HorizonMeter h={horizon(t, 900)} /></td>
            <td class="muted hide-sm hide-md" title={dateTime(t.episode_start_ms)}
              >{ago(t.episode_start_ms, clock.now)}</td
            >
            <td class="muted" title={dateTime(t.detected_at_ms)}>{ago(t.detected_at_ms, clock.now)}</td>
            <td class="right num hide-sm" data-testid="delay">{delay(delayMs(t))}</td>
            <td class="right num hide-sm muted" data-testid="revision">{t.revision}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  {#if waiting}
    <EmptyState icon="radio" title="Connecting to the live feed…" testid="waiting">
      Incidents appear as soon as the first snapshot arrives.
    </EmptyState>
  {:else if all.length === 0}
    <EmptyState title="All quiet" testid="empty">
      No incidents right now. New detections show up here instantly.
    </EmptyState>
  {:else if rows.length === 0}
    <EmptyState icon="search" title="Nothing matches these filters" testid="empty-filtered">
      {all.length} incidents are hidden by the severity or resolved filter.
    </EmptyState>
  {/if}
</section>

<style>
  .threat strong {
    font-weight: 600;
  }
  .why {
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--muted);
    font-size: 12px;
  }
  tr.resolved .threat strong {
    font-weight: 500;
  }
  .device {
    font-weight: 600;
    color: var(--text);
  }
  .device:hover {
    color: var(--accent);
    text-decoration: none;
  }
  tr.resolved td {
    color: var(--muted);
  }
  tr.resolved .device {
    color: var(--muted);
    font-weight: 500;
  }
  tr:not(.resolved) td:first-child {
    box-shadow: inset 3px 0 0 var(--high);
  }
  tr.flash {
    animation: flash 2.5s var(--ease);
  }
  @keyframes flash {
    0%,
    30% {
      background: color-mix(in oklch, var(--accent) 16%, transparent);
    }
    100% {
      background: transparent;
    }
  }
</style>
