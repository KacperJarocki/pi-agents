<script lang="ts">
  import type { Snippet } from 'svelte';
  import Icon, { type IconName } from './Icon.svelte';

  let {
    tone = 'warn',
    title,
    icon = 'alert',
    testid,
    role = 'status',
    children,
    actions,
  }: {
    tone?: 'warn' | 'danger' | 'info';
    title: string;
    icon?: IconName;
    testid?: string;
    role?: 'status' | 'alert';
    children?: Snippet;
    actions?: Snippet;
  } = $props();
</script>

<div class="banner tone-{tone}" data-testid={testid} {role}>
  <span class="icon"><Icon name={icon} size={18} /></span>
  <div class="body">
    <strong>{title}</strong>
    {#if children}<div class="detail">{@render children()}</div>{/if}
  </div>
  {#if actions}<div class="actions">{@render actions()}</div>{/if}
</div>

<style>
  .banner {
    --c: var(--warn);
    display: flex;
    gap: 12px;
    align-items: flex-start;
    padding: 12px 16px;
    margin-bottom: 16px;
    border-radius: var(--radius);
    border: 1px solid color-mix(in oklch, var(--c) 35%, transparent);
    background: color-mix(in oklch, var(--c) 9%, var(--surface));
    animation: enter 220ms var(--ease);
  }
  .tone-danger {
    --c: var(--danger);
  }
  .tone-info {
    --c: var(--accent);
  }
  .icon {
    color: var(--c);
    padding-top: 1px;
  }
  .body {
    flex: 1;
    min-width: 0;
  }
  .detail {
    color: var(--muted);
    margin-top: 2px;
  }
  .detail :global(ul) {
    margin: 4px 0 0;
    padding-left: 18px;
  }
  .actions {
    flex: none;
  }
  @keyframes enter {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
  }
</style>
