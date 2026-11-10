<script lang="ts">
  import type { Snippet } from 'svelte';
  import Icon, { type IconName } from './Icon.svelte';

  let {
    icon = 'shield',
    title,
    testid,
    children,
  }: { icon?: IconName; title: string; testid?: string; children?: Snippet } = $props();
</script>

<div class="empty" data-testid={testid}>
  <span class="icon"><Icon name={icon} size={26} /></span>
  <strong>{title}</strong>
  {#if children}<p>{@render children()}</p>{/if}
</div>

<style>
  .empty {
    display: grid;
    justify-items: center;
    gap: 6px;
    padding: 48px 16px;
    text-align: center;
  }
  .icon {
    display: grid;
    place-items: center;
    width: 52px;
    height: 52px;
    margin-bottom: 6px;
    border-radius: 16px;
    color: var(--ok);
    background: color-mix(in oklch, var(--ok) 12%, transparent);
  }
  strong {
    font-size: 15px;
  }
  p {
    margin: 0;
    color: var(--muted);
    max-width: 42ch;
  }
</style>
