<script lang="ts">
  import { api } from '../lib/api';
  import { clock } from '../lib/clock.svelte';
  import { bytes, clockTime, count, dateTime } from '../lib/format';
  import { live } from '../lib/live.svelte';
  import type { HistoryPoint, Transition } from '../lib/types';
  import FilterChips from './FilterChips.svelte';

  // A device's per-minute 60 s windows as two small multiples on one time
  // axis: failed targets (the port-scan signal, with its open threshold) and
  // flows. Incident episodes are shaded on both.
  let {
    deviceKey,
    incidents = [],
    threshold = 20,
  }: { deviceKey: string; incidents?: Transition[]; threshold?: number } = $props();

  type Range = '60' | '360' | '1440';
  const RANGES: { value: Range; label: string; tickMin: number }[] = [
    { value: '60', label: '1 h', tickMin: 10 },
    { value: '360', label: '6 h', tickMin: 60 },
    { value: '1440', label: '24 h', tickMin: 240 },
  ];
  const REFRESH_MS = 30_000;
  const GAP_MS = 90_000; // a missing minute breaks the line
  const PLOT_H = 96;
  const PAD = { left: 40, right: 12, top: 18, bottom: 6 };
  const AXIS_H = 22;

  let range = $state<Range>('60');
  let points = $state<HistoryPoint[] | null>(null);
  let error = $state<string | null>(null);
  let width = $state(480);
  let hover = $state<number | null>(null);

  async function load(key: string, minutes: number) {
    try {
      const body = await api.history(key, minutes);
      if (key !== deviceKey) return;
      points = body.points ?? [];
      error = null;
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  $effect(() => {
    const key = deviceKey;
    const minutes = Number(range);
    void live.changes;
    points = null;
    hover = null;
    void load(key, minutes);
    const timer = setInterval(() => void load(key, minutes), REFRESH_MS);
    return () => clearInterval(timer);
  });

  const spec = $derived(RANGES.find((r) => r.value === range) ?? RANGES[0]!);
  // The axis ends at the current minute and only moves when the minute does.
  const end = $derived(Math.floor(clock.now / 60_000) * 60_000 + 60_000);
  const start = $derived(end - Number(range) * 60_000);
  const innerW = $derived(Math.max(10, width - PAD.left - PAD.right));
  const x = (t: number) => PAD.left + ((t - start) / (end - start)) * innerW;

  const series = [
    { key: 'failed_targets', title: 'Failed targets', hint: 'refused or unanswered, per 60 s window', limit: true },
    { key: 'flows', title: 'Flows', hint: 'per 60 s window', limit: false },
  ] as const;
  type Key = (typeof series)[number]['key'];

  const value = (p: HistoryPoint, key: Key) => p[key] ?? 0;

  function niceMax(v: number): number {
    if (v <= 0) return 1;
    const mag = 10 ** Math.floor(Math.log10(v));
    const step = [1, 2, 2.5, 5, 10].find((s) => s * mag >= v) ?? 10;
    return step * mag;
  }

  const visible = $derived((points ?? []).filter((p) => p.minute_ms >= start && p.minute_ms <= end));

  function scale(key: Key, limit: boolean) {
    const max = Math.max(0, ...visible.map((p) => value(p, key)));
    const top = niceMax(Math.max(max, limit ? threshold * 1.25 : 0));
    return { top, y: (v: number) => PAD.top + PLOT_H - (Math.min(v, top) / top) * PLOT_H };
  }

  function segments(key: Key, y: (v: number) => number): { line: string; area: string }[] {
    const out: { line: string; area: string }[] = [];
    let run: HistoryPoint[] = [];
    const flush = () => {
      if (!run.length) return;
      const pts = run.map((p) => `${x(p.minute_ms).toFixed(1)},${y(value(p, key)).toFixed(1)}`);
      const base = (PAD.top + PLOT_H).toFixed(1);
      const first = x(run[0]!.minute_ms).toFixed(1);
      const last = x(run[run.length - 1]!.minute_ms).toFixed(1);
      out.push({
        line: run.length === 1 ? '' : `M${pts.join('L')}`,
        area: run.length === 1 ? '' : `M${first},${base}L${pts.join('L')}L${last},${base}Z`,
      });
      run = [];
    };
    for (const p of visible) {
      const prev = run[run.length - 1];
      if (prev && p.minute_ms - prev.minute_ms > GAP_MS) flush();
      run.push(p);
    }
    flush();
    return out;
  }

  const bands = $derived(
    incidents
      .filter((t) => t.episode_start_ms)
      .map((t) => {
        const from = Math.max(t.episode_start_ms!, start);
        const to = Math.min(t.status === 'RESOLVE' ? (t.detected_at_ms ?? end) : end, end);
        return { id: t.incident_id, from, to, severity: t.severity };
      })
      .filter((b) => b.to > b.from),
  );

  const ticks = $derived.by(() => {
    const step = spec.tickMin * 60_000;
    const out: number[] = [];
    for (let t = Math.ceil(start / step) * step; t <= end; t += step) out.push(t);
    return out;
  });
  const tickLabel = (t: number) => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  const hovered = $derived(hover === null ? null : (visible[hover] ?? null));
  const hoveredIncident = $derived(
    hovered ? bands.find((b) => hovered.minute_ms >= b.from && hovered.minute_ms <= b.to) : undefined,
  );

  function nearest(clientX: number, rect: DOMRect) {
    if (!visible.length) return;
    const t = start + ((clientX - rect.left - PAD.left) / innerW) * (end - start);
    let best = 0;
    for (let i = 1; i < visible.length; i++) {
      if (Math.abs(visible[i]!.minute_ms - t) < Math.abs(visible[best]!.minute_ms - t)) best = i;
    }
    hover = best;
  }

  function onKey(e: KeyboardEvent) {
    if (!visible.length) return;
    if (e.key === 'ArrowLeft') hover = Math.max(0, (hover ?? visible.length) - 1);
    else if (e.key === 'ArrowRight') hover = Math.min(visible.length - 1, (hover ?? -1) + 1);
    else if (e.key === 'Escape') hover = null;
    else return;
    e.preventDefault();
  }

  const valueText = $derived.by(() => {
    const p = hovered ?? visible[visible.length - 1];
    if (!p) return '';
    return `${clockTime(p.minute_ms)}: ${count(p.failed_targets)} failed targets, ${count(p.flows)} flows${hoveredIncident ? ', incident open' : ''}`;
  });

  const peak = $derived(Math.max(0, ...visible.map((p) => value(p, 'failed_targets'))));
  const summary = $derived(
    visible.length
      ? `Last ${spec.label}: failed targets peaked at ${Math.round(peak)} per minute (threshold ${threshold}); ${bands.length} incident ${bands.length === 1 ? 'episode' : 'episodes'}.`
      : `No traffic recorded in the last ${spec.label}.`,
  );
</script>

<section class="traffic" data-testid="traffic-chart">
  <header>
    <h3>Traffic</h3>
    <FilterChips
      label="Time range"
      bind:value={range}
      options={RANGES.map((r) => ({ value: r.value, label: r.label }))}
    />
  </header>

  {#if points === null}
    {#if error}
      <p class="muted">Could not load the traffic history ({error}).</p>
    {:else}
      <span class="skeleton chart-skeleton"></span>
    {/if}
  {:else if visible.length === 0}
    <p class="empty muted" data-testid="traffic-empty">No traffic recorded in the last {spec.label}.</p>
  {:else}
    <div
      class="plot"
      bind:clientWidth={width}
      role="slider"
      aria-roledescription="chart"
      aria-label="{summary} Use the left and right arrow keys to read each minute."
      aria-valuemin={0}
      aria-valuemax={visible.length - 1}
      aria-valuenow={hover ?? visible.length - 1}
      aria-valuetext={valueText}
      tabindex="0"
      onpointermove={(e) => nearest(e.clientX, e.currentTarget.getBoundingClientRect())}
      onpointerleave={() => (hover = null)}
      onkeydown={onKey}
      onblur={() => (hover = null)}
    >
      {#each series as s, i (s.key)}
        {@const sc = scale(s.key, s.limit)}
        {@const last = i === series.length - 1}
        <div class="panel">
          <div class="panel-title">
            <strong>{s.title}</strong> <span class="muted">{s.hint}</span>
          </div>
          <svg {width} height={PAD.top + PLOT_H + PAD.bottom + (last ? AXIS_H : 0)} aria-hidden="true">
            {#each bands as b (b.id)}
              <rect
                class="band band-{b.severity.toLowerCase()}"
                x={x(b.from)}
                y={PAD.top}
                width={Math.max(2, x(b.to) - x(b.from))}
                height={PLOT_H}
              />
            {/each}
            {#each [0, sc.top / 2, sc.top] as g (g)}
              <line class="grid" x1={PAD.left} x2={width - PAD.right} y1={sc.y(g)} y2={sc.y(g)} />
              <text class="tick" x={PAD.left - 6} y={sc.y(g) + 4} text-anchor="end">{count(g)}</text>
            {/each}
            {#if s.limit}
              <line class="limit" x1={PAD.left} x2={width - PAD.right} y1={sc.y(threshold)} y2={sc.y(threshold)} />
              <text class="limit-label" x={width - PAD.right} y={sc.y(threshold) - 5} text-anchor="end"
                >opens at {threshold}</text
              >
            {/if}
            {#each segments(s.key, sc.y) as seg, j (j)}
              <path class="area" d={seg.area} />
              <path class="line" d={seg.line} />
            {/each}
            {#if hovered}
              <line
                class="crosshair"
                x1={x(hovered.minute_ms)}
                x2={x(hovered.minute_ms)}
                y1={PAD.top}
                y2={PAD.top + PLOT_H}
              />
              <circle class="dot" cx={x(hovered.minute_ms)} cy={sc.y(value(hovered, s.key))} r="4" />
            {/if}
            {#if last}
              {#each ticks as t (t)}
                <text class="tick" x={x(t)} y={PAD.top + PLOT_H + PAD.bottom + 14} text-anchor="middle"
                  >{tickLabel(t)}</text
                >
              {/each}
            {/if}
          </svg>
        </div>
      {/each}

      {#if hovered}
        <div
          class="tooltip"
          class:flip={x(hovered.minute_ms) > width / 2}
          style:left="{x(hovered.minute_ms)}px"
          data-testid="traffic-tooltip"
        >
          <div class="tt-time">{clockTime(hovered.minute_ms)}</div>
          <dl>
            <dt>Failed targets</dt>
            <dd class="num">{count(hovered.failed_targets)}</dd>
            <dt>Flows</dt>
            <dd class="num">{count(hovered.flows)}</dd>
            <dt>Targets</dt>
            <dd class="num">{count(hovered.distinct_targets)}</dd>
            <dt>Traffic</dt>
            <dd class="num">{bytes(hovered.bytes)}</dd>
          </dl>
          {#if hoveredIncident}<div class="tt-incident">Incident open</div>{/if}
        </div>
      {/if}
    </div>

    <details class="as-table">
      <summary>Show as table</summary>
      <div class="table-scroll">
        <table class="data">
          <thead>
            <tr>
              <th>Minute</th>
              <th class="right">Failed targets</th>
              <th class="right">Flows</th>
              <th class="right">Targets</th>
              <th class="right">Traffic</th>
            </tr>
          </thead>
          <tbody>
            {#each [...visible].reverse() as p (p.minute_ms)}
              <tr>
                <td title={dateTime(p.minute_ms)}>{clockTime(p.minute_ms)}</td>
                <td class="right num">{count(p.failed_targets)}</td>
                <td class="right num">{count(p.flows)}</td>
                <td class="right num">{count(p.distinct_targets)}</td>
                <td class="right num">{bytes(p.bytes)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </details>
  {/if}
</section>

<style>
  .traffic {
    margin-bottom: 24px;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 8px;
  }
  h3 {
    font-size: 15px;
  }
  .chart-skeleton {
    height: 260px;
    border-radius: var(--radius);
  }
  .empty {
    padding: 28px 0;
    text-align: center;
    border: 1px dashed var(--border-strong);
    border-radius: var(--radius);
  }
  .plot {
    position: relative;
    padding: 8px 0 4px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    cursor: crosshair;
    touch-action: pan-y;
  }
  .panel-title {
    padding: 4px 12px 0;
    font-size: 12.5px;
  }
  svg {
    display: block;
    overflow: visible;
  }
  .grid {
    stroke: var(--border);
    stroke-width: 1;
  }
  .tick {
    fill: var(--muted);
    font-size: 10.5px;
    font-variant-numeric: tabular-nums;
  }
  .limit {
    stroke: var(--high);
    stroke-width: 1.5;
    stroke-dasharray: 5 4;
  }
  .limit-label {
    fill: var(--muted);
    font-size: 11px;
  }
  .band {
    fill: color-mix(in oklch, var(--high) 9%, transparent);
  }
  .band-medium {
    fill: color-mix(in oklch, var(--medium) 11%, transparent);
  }
  .band-low {
    fill: color-mix(in oklch, var(--low) 11%, transparent);
  }
  .area {
    fill: color-mix(in oklch, var(--accent) 10%, transparent);
  }
  .line {
    fill: none;
    stroke: var(--accent);
    stroke-width: 2;
    stroke-linejoin: round;
    stroke-linecap: round;
  }
  .crosshair {
    stroke: var(--border-strong);
    stroke-width: 1;
  }
  .dot {
    fill: var(--accent);
    stroke: var(--surface);
    stroke-width: 2;
  }
  /* Beside the crosshair, on the side with room, so it never hides the data. */
  .tooltip {
    position: absolute;
    top: 40px;
    translate: 12px 0;
    min-width: 160px;
    padding: 8px 10px;
    border-radius: 8px;
    background: var(--surface);
    border: 1px solid var(--border-strong);
    box-shadow: var(--shadow);
    font-size: 12px;
    pointer-events: none;
  }
  .tooltip.flip {
    translate: calc(-100% - 12px) 0;
  }
  .tt-time {
    font-weight: 600;
    margin-bottom: 4px;
  }
  .tooltip dl {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 2px 12px;
    margin: 0;
  }
  .tooltip dt {
    color: var(--muted);
  }
  .tooltip dd {
    margin: 0;
    text-align: right;
    font-weight: 600;
  }
  .tt-incident {
    margin-top: 6px;
    color: var(--high);
    font-weight: 600;
  }
  .as-table {
    margin-top: 8px;
  }
  .as-table summary {
    cursor: pointer;
    color: var(--muted);
    font-size: 12.5px;
  }
  .table-scroll {
    max-height: 240px;
    overflow: auto;
    margin-top: 6px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
</style>
