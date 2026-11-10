<script lang="ts">
  import { clock } from '../lib/clock.svelte';
  import { deviceName, deviceState, matches, type DeviceStateKind } from '../lib/device-state';
  import { devices } from '../lib/devices.svelte';
  import { ago, bytes, count, dateTime } from '../lib/format';
  import { deviceHref, link, navigate, route } from '../lib/router.svelte';
  import type { Device } from '../lib/types';
  import Badge from './Badge.svelte';
  import Banner from './Banner.svelte';
  import DeviceDrawer from './DeviceDrawer.svelte';
  import EmptyState from './EmptyState.svelte';
  import FilterChips from './FilterChips.svelte';
  import Icon from './Icon.svelte';
  import StatTile from './StatTile.svelte';

  type Filter = 'all' | 'alert' | 'active' | 'quiet';
  const SEP = ' / ';
  const SKELETON_COLS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

  let query = $state('');
  let filter = $state<Filter>('all');

  const rows = $derived(devices.devices.map((d) => ({ d, st: deviceState(d, clock.now) })));
  const inFilter = (kind: DeviceStateKind, f: Filter) =>
    f === 'all' ||
    (f === 'alert' && kind === 'alert') ||
    (f === 'active' && (kind === 'clean' || kind === 'alert')) ||
    (f === 'quiet' && (kind === 'idle' || kind === 'unseen'));
  const countFor = (f: Filter) => rows.filter((r) => inFilter(r.st.kind, f)).length;
  const visible = $derived(rows.filter((r) => inFilter(r.st.kind, filter) && matches(r.d, query)));

  const alerting = $derived(rows.filter((r) => r.st.kind === 'alert').length);
  const online = $derived(countFor('active'));
  const openIncidents = $derived(devices.devices.reduce((n, d) => n + (d.incidents.active ?? 0), 0));
  const totalIncidents = $derived(devices.devices.reduce((n, d) => n + (d.incidents.total ?? 0), 0));
  const traffic = $derived(devices.devices.reduce((n, d) => n + (d.features['900']?.bytes ?? 0), 0));

  function open(d: Device, event: MouseEvent) {
    if (event.defaultPrevented || !d.device_key) return;
    navigate(deviceHref(d.device_key));
  }
</script>

<div class="page-head">
  <div>
    <h1>Devices</h1>
    <p>Every client on the gateway's Wi-Fi, with its latest traffic windows and incidents.</p>
  </div>
  <span class="page-meta" data-testid="devices-status" class:error={devices.error}>
    {#if devices.error}
      <Icon name="alert" size={14} />Update failed · retrying
    {:else if devices.updatedAt}
      <Icon name="refresh" size={14} />Updated {ago(devices.updatedAt, clock.now)}
    {:else}
      Loading…
    {/if}
  </span>
</div>

{#if devices.leases && !devices.leases.available}
  <Banner tone="info" icon="info" title="Hostnames and IPs unavailable" testid="lease-note">
    {devices.leases.error}. Devices are shown by MAC address.
  </Banner>
{/if}

{#if devices.error && devices.devices.length === 0 && devices.loaded}
  <Banner tone="danger" role="alert" title="Could not load devices" testid="devices-error">
    {devices.error}. Retrying every few seconds.
  </Banner>
{/if}

<section class="tiles" aria-label="Summary">
  <StatTile
    icon="router"
    label="Devices"
    value={devices.devices.length}
    hint="{online} active in the last 2 min"
    tone="accent"
  />
  <StatTile
    icon="alert"
    label="Alerting"
    value={alerting}
    hint={alerting ? 'devices with open incidents' : 'nothing suspicious'}
    tone={alerting ? 'high' : 'ok'}
  />
  <StatTile icon="activity" label="Open incidents" value={openIncidents} hint="{totalIncidents} recorded in total" />
  <StatTile icon="network" label="Traffic · 15 min" value={bytes(traffic)} hint="across all devices" />
</section>

<section class="card">
  <div class="toolbar">
    <label class="search">
      <Icon name="search" />
      <span class="visually-hidden">Search devices</span>
      <input type="search" placeholder="Search name, IP or MAC" bind:value={query} data-testid="device-search" />
    </label>
    <span class="spacer"></span>
    <FilterChips
      label="Device state"
      bind:value={filter}
      options={[
        { value: 'all', label: 'All', count: rows.length },
        { value: 'alert', label: 'Alerting', count: alerting },
        { value: 'active', label: 'Active', count: online },
        { value: 'quiet', label: 'Idle', count: countFor('quiet') },
      ]}
    />
  </div>

  <div class="table-wrap">
    <table class="data">
      <thead>
        <tr>
          <th>State</th>
          <th>Device</th>
          <th>IP</th>
          <th>Last seen</th>
          <th class="right" title="Flows in the last 60 s">Flows · 60 s</th>
          <th class="right hide-sm hide-md" title="Distinct targets in the last 60 s">Targets · 60 s</th>
          <th class="right hide-sm" title="Failed targets in the last 60 s">Failed · 60 s</th>
          <th class="right hide-sm hide-md">Flows · 15 min</th>
          <th class="right hide-sm">Traffic · 15 min</th>
          <th class="right" title="Open / total incidents">Incidents</th>
          <th aria-hidden="true"></th>
        </tr>
      </thead>
      <tbody data-testid="devices">
        {#if !devices.loaded}
          {#each [1, 2, 3, 4] as i (i)}
            <tr class="skeleton-row" aria-hidden="true">
              {#each SKELETON_COLS as j (j)}<td
                  ><span class="skeleton" style:width="{40 + ((i * 7 + j * 13) % 50)}%"></span></td
                >{/each}
            </tr>
          {/each}
        {:else}
          {#each visible as { d, st } (d.device_key ?? d.mac)}
            {@const f60 = d.features['60'] ?? {}}
            {@const f900 = d.features['900'] ?? {}}
            {@const total = d.incidents.total ?? 0}
            <tr
              data-device={d.device_key || d.mac}
              class:clickable={Boolean(d.device_key)}
              class:selected={route.device === d.device_key}
              class:alerting={st.kind === 'alert'}
              onclick={(e) => open(d, e)}
            >
              <td>
                <Badge tone={st.tone} dot pulse={st.kind === 'alert'} testid="state">{st.label}</Badge>
              </td>
              <td>
                {#if d.device_key}
                  <a class="name" href={deviceHref(d.device_key)} onclick={link} data-testid="name">{deviceName(d)}</a>
                {:else}
                  <span class="name" data-testid="name">{deviceName(d)}</span>
                {/if}
                <div class="mono muted">{d.mac}</div>
              </td>
              <td class="mono" data-testid="ip">{d.ip || '–'}</td>
              <td title={dateTime(d.last_seen_ms)} class="muted">{ago(d.last_seen_ms, clock.now)}</td>
              <td class="right num">{count(f60.flows)}</td>
              <td class="right num hide-sm hide-md">{count(f60.distinct_targets)}</td>
              <td class="right num hide-sm" class:warn={(f60.failed_targets ?? 0) > 0}>{count(f60.failed_targets)}</td>
              <td class="right num hide-sm hide-md">{count(f900.flows)}</td>
              <td class="right num hide-sm">{bytes(f900.bytes)}</td>
              <td class="right num" data-testid="incident-count">
                {#if d.incidents.active}<span class="open">{d.incidents.active}</span>{SEP}{/if}{total}
              </td>
              <td class="chev"
                >{#if d.device_key}<Icon name="chevron" />{/if}</td
              >
            </tr>
          {/each}
        {/if}
      </tbody>
    </table>
  </div>

  {#if devices.loaded && devices.devices.length === 0}
    <EmptyState icon="router" title="No devices seen yet" testid="devices-empty">
      Devices appear here once they join the gateway's Wi-Fi or send traffic through it.
    </EmptyState>
  {:else if devices.loaded && visible.length === 0}
    <EmptyState icon="search" title="No matching devices" testid="devices-filtered">
      Try a different search or state filter.
      <button class="btn reset" type="button" onclick={() => ((query = ''), (filter = 'all'))}>Clear filters</button>
    </EmptyState>
  {/if}
</section>

{#if route.device}
  <DeviceDrawer deviceKey={route.device} />
{/if}

<style>
  .page-meta.error {
    color: var(--danger);
  }
  .name {
    font-weight: 600;
    color: var(--text);
  }
  a.name:hover {
    color: var(--accent);
    text-decoration: none;
  }
  tr.selected {
    background: color-mix(in oklch, var(--accent) 8%, transparent);
  }
  tr.alerting td:first-child {
    box-shadow: inset 3px 0 0 var(--high);
  }
  .warn {
    color: var(--medium);
    font-weight: 600;
  }
  .open {
    display: inline-block;
    min-width: 20px;
    padding: 0 6px;
    border-radius: 99px;
    text-align: center;
    font-weight: 700;
    color: var(--accent-contrast);
    background: var(--high);
  }
  .chev {
    width: 28px;
    color: var(--faint);
  }
  tr.clickable:hover .chev {
    color: var(--accent);
  }
  .reset {
    margin-top: 12px;
  }
</style>
