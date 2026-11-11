<script lang="ts" generics="T extends string">
  // Single-select segmented filter with optional counts.
  let {
    options,
    value = $bindable(),
    label,
  }: { options: { value: T; label: string; count?: number }[]; value: T; label: string } = $props();
</script>

<div class="chips" role="radiogroup" aria-label={label}>
  {#each options as o (o.value)}
    <button
      type="button"
      role="radio"
      aria-checked={value === o.value}
      class:active={value === o.value}
      onclick={() => (value = o.value)}
    >
      {o.label}{#if o.count !== undefined}<span class="count num">{o.count}</span>{/if}
    </button>
  {/each}
</div>

<style>
  .chips {
    display: inline-flex;
    gap: 2px;
    padding: 3px;
    border-radius: 10px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    max-width: 100%;
    overflow-x: auto;
    scrollbar-width: none;
  }
  button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border: 0;
    background: transparent;
    padding: 5px 11px;
    border-radius: 7px;
    color: var(--muted);
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
    transition:
      background-color 120ms,
      color 120ms;
  }
  button:hover {
    color: var(--text);
  }
  button.active {
    background: var(--surface);
    color: var(--text);
    box-shadow: 0 1px 2px oklch(0% 0 0 / 0.12);
  }
  .count {
    font-size: 11px;
    padding: 0 6px;
    border-radius: 99px;
    background: color-mix(in oklch, var(--muted) 15%, transparent);
  }
</style>
