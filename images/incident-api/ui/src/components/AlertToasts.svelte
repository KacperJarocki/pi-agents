<script lang="ts">
  import { alerts } from '../lib/alerts.svelte';
  import { clockTime } from '../lib/format';
  import { link } from '../lib/router.svelte';
  import Icon from './Icon.svelte';
</script>

<div class="toasts" aria-live="assertive" data-testid="alert-toasts">
  {#each alerts.toasts as toast (toast.id)}
    <div class="toast sev-{toast.severity.toLowerCase()}" role="alert" data-testid="alert-toast">
      <span class="icon"><Icon name="alert" size={18} /></span>
      <a
        class="body"
        href="/live/"
        onclick={(e) => {
          link(e);
          alerts.dismiss(toast.id);
        }}
      >
        <strong>{toast.title}</strong>
        <span class="meta">Device <span class="num">{toast.device}</span> · {clockTime(toast.at)}</span>
      </a>
      <button type="button" class="close" aria-label="Dismiss alert" onclick={() => alerts.dismiss(toast.id)}>
        <Icon name="x" size={14} />
      </button>
    </div>
  {/each}
</div>

<style>
  .toasts {
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 50;
    display: grid;
    gap: 10px;
    width: min(380px, calc(100vw - 32px));
    pointer-events: none;
  }
  .toast {
    --c: var(--low);
    pointer-events: auto;
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 12px 12px 12px 14px;
    border-radius: var(--radius);
    border: 1px solid color-mix(in oklch, var(--c) 45%, transparent);
    border-left: 4px solid var(--c);
    background: color-mix(in oklch, var(--c) 10%, var(--surface));
    box-shadow: 0 10px 30px -12px oklch(0% 0 0 / 0.35);
    animation: enter 220ms var(--ease);
  }
  .sev-medium {
    --c: var(--medium);
  }
  .sev-high {
    --c: var(--high);
  }
  .icon {
    color: var(--c);
    padding-top: 1px;
  }
  .body {
    flex: 1;
    min-width: 0;
    display: grid;
    gap: 2px;
    color: var(--text);
    text-decoration: none;
  }
  .meta {
    font-size: 12.5px;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .close {
    display: grid;
    place-items: center;
    width: 24px;
    height: 24px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
  }
  @keyframes enter {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .toast {
      animation: none;
    }
  }
  .close:hover {
    color: var(--text);
    background: var(--surface-2);
  }
</style>
