<script lang="ts">
  import type { Horizon } from '../lib/types';

  // value / threshold with a small bar; the bar fills at the open threshold.
  let { h }: { h: Horizon | undefined } = $props();
  const ratio = $derived(h && h.open_threshold > 0 ? Math.min(h.value / h.open_threshold, 1) : 0);
</script>

{#if h}
  <span
    class="meter"
    class:over={h.over_threshold}
    title="{h.value} of {h.open_threshold} needed to open ({h.horizon_seconds} s window)"
  >
    <span class="text num"><span class="value">{h.value}</span><span class="of">/ {h.open_threshold}</span></span>
    <span class="bar" aria-hidden="true"><span class="fill" style:inline-size="{ratio * 100}%"></span></span>
  </span>
{:else}
  <span class="muted">–</span>
{/if}

<style>
  .meter {
    display: inline-grid;
    gap: 4px;
    min-width: 72px;
  }
  .text {
    font-size: 13px;
  }
  .of {
    color: var(--muted);
    margin-left: 3px;
    font-size: 12px;
  }
  .over .value {
    color: var(--danger);
    font-weight: 700;
  }
  .bar {
    display: block;
    height: 4px;
    border-radius: 99px;
    background: var(--surface-2);
    overflow: hidden;
  }
  .fill {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--faint);
    transition: inline-size 400ms var(--ease);
  }
  .over .fill {
    background: var(--danger);
  }
</style>
