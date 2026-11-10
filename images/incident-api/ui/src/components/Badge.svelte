<script lang="ts">
  import type { Snippet } from 'svelte';

  export type Tone = 'high' | 'medium' | 'low' | 'ok' | 'idle' | 'accent' | 'neutral';

  let {
    tone = 'neutral',
    dot = false,
    pulse = false,
    solid = false,
    testid,
    title,
    children,
  }: {
    tone?: Tone;
    dot?: boolean;
    pulse?: boolean;
    solid?: boolean;
    testid?: string;
    title?: string;
    children: Snippet;
  } = $props();
</script>

<span class="badge tone-{tone}" class:solid data-testid={testid} {title}>
  {#if dot}<span class="dot" class:pulse aria-hidden="true"></span>{/if}<span class="text">{@render children()}</span>
</span>

<style>
  .badge {
    --c: var(--muted);
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    line-height: 18px;
    color: var(--c);
    background: color-mix(in oklch, var(--c) 13%, transparent);
    border: 1px solid color-mix(in oklch, var(--c) 22%, transparent);
    white-space: nowrap;
  }
  .solid {
    color: var(--accent-contrast);
    background: var(--c);
    border-color: transparent;
  }
  .tone-high {
    --c: var(--high);
  }
  .tone-medium {
    --c: var(--medium);
  }
  .tone-low {
    --c: var(--low);
  }
  .tone-ok {
    --c: var(--ok);
  }
  .tone-idle {
    --c: var(--idle);
  }
  .tone-accent {
    --c: var(--accent);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: currentColor;
  }
  .solid .dot {
    background: var(--accent-contrast);
  }
  .pulse {
    animation: pulse 1.6s var(--ease) infinite;
  }
  @keyframes pulse {
    0% {
      box-shadow: 0 0 0 0 color-mix(in oklch, var(--c) 60%, transparent);
    }
    100% {
      box-shadow: 0 0 0 7px transparent;
    }
  }
</style>
