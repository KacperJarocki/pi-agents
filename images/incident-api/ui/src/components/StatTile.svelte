<script lang="ts">
  import Icon, { type IconName } from './Icon.svelte';

  let {
    label,
    value,
    hint = '',
    icon,
    tone = 'neutral',
    testid,
  }: {
    label: string;
    value: string | number;
    hint?: string;
    icon: IconName;
    tone?: 'neutral' | 'high' | 'ok' | 'accent';
    testid?: string;
  } = $props();
</script>

<div class="tile card tone-{tone}" data-testid={testid}>
  <span class="icon"><Icon name={icon} size={18} /></span>
  <div>
    <div class="label">{label}</div>
    <div class="value num">{value}</div>
    {#if hint}<div class="hint">{hint}</div>{/if}
  </div>
</div>

<style>
  .tile {
    --c: var(--muted);
    display: flex;
    gap: 14px;
    align-items: flex-start;
    padding: 16px 18px;
  }
  .tone-high {
    --c: var(--high);
  }
  .tone-ok {
    --c: var(--ok);
  }
  .tone-accent {
    --c: var(--accent);
  }
  .icon {
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    border-radius: 10px;
    color: var(--c);
    background: color-mix(in oklch, var(--c) 12%, transparent);
  }
  .label {
    color: var(--muted);
    font-size: 12.5px;
    font-weight: 500;
  }
  .value {
    font-size: 24px;
    font-weight: 650;
    letter-spacing: -0.02em;
    line-height: 1.2;
  }
  .tone-high .value {
    color: var(--high);
  }
  .hint {
    color: var(--muted);
    font-size: 12px;
  }
  @media (max-width: 760px) {
    .tile {
      padding: 12px;
      gap: 10px;
    }
    .icon {
      width: 30px;
      height: 30px;
    }
    .value {
      font-size: 20px;
    }
    .hint {
      display: none;
    }
  }
</style>
