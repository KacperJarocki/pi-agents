<script lang="ts">
  import { clock } from '../lib/clock.svelte';
  import { live } from '../lib/live.svelte';
  import { prefs, type Theme } from '../lib/prefs.svelte';
  import { link, route } from '../lib/router.svelte';
  import Icon from './Icon.svelte';

  const active = $derived(live.active.length);
  const retryIn = $derived(live.retryAt ? Math.max(0, Math.ceil((live.retryAt - clock.now) / 1000)) : 0);
  const statusText = $derived(
    live.connection === 'live' ? 'Live' : live.connection === 'connecting' ? 'Connecting…' : 'Offline',
  );
  const statusHint = $derived(
    live.connection === 'live'
      ? `Receiving incidents in real time (cursor ${live.cursor})`
      : live.connection === 'offline'
        ? `Connection lost, retrying in ${retryIn} s`
        : 'Opening the live connection',
  );

  const themes: { value: Theme; label: string; icon: 'monitor' | 'sun' | 'moon' }[] = [
    { value: 'system', label: 'System theme', icon: 'monitor' },
    { value: 'light', label: 'Light theme', icon: 'sun' },
    { value: 'dark', label: 'Dark theme', icon: 'moon' },
  ];
  const theme = $derived(themes.find((t) => t.value === prefs.theme) ?? themes[0]!);
  const nextTheme = () => {
    const i = themes.findIndex((t) => t.value === prefs.theme);
    prefs.theme = themes[(i + 1) % themes.length]!.value;
  };
</script>

<header class="app-header">
  <div class="inner">
    <a class="brand" href="/" onclick={link} aria-label="pi-agents home">
      <span class="logo"><Icon name="shield" size={18} /></span>
      <span class="name">pi-agents<span class="sub">IoT threat monitor</span></span>
    </a>

    <nav aria-label="Main">
      <a href="/" onclick={link} aria-current={route.page === 'devices' ? 'page' : undefined}>
        <Icon name="router" />Devices
      </a>
      <a href="/live/" onclick={link} aria-current={route.page === 'live' ? 'page' : undefined}>
        <Icon name="activity" />Live incidents
        {#if active > 0}<span class="count num" data-testid="nav-active-count">{active}</span>{/if}
      </a>
    </nav>

    <div class="tools">
      <span
        class="conn conn-{live.connection}"
        data-testid="status"
        data-cursor={live.cursor ?? ''}
        title={statusHint}
        aria-live="polite"
      >
        <span class="dot" aria-hidden="true"></span>{statusText}
      </span>
      <button class="icon-btn" type="button" onclick={nextTheme} title={theme.label} aria-label={theme.label}>
        <Icon name={theme.icon} />
      </button>
    </div>
  </div>
</header>

<style>
  .app-header {
    position: sticky;
    top: 0;
    z-index: 10;
    background: color-mix(in oklch, var(--bg) 82%, transparent);
    backdrop-filter: saturate(1.6) blur(12px);
    border-bottom: 1px solid var(--border);
  }
  .inner {
    max-width: 1240px;
    margin: 0 auto;
    padding: 10px 24px;
    display: flex;
    align-items: center;
    gap: 24px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--text);
    text-decoration: none;
  }
  .logo {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 9px;
    color: var(--accent-contrast);
    background: linear-gradient(135deg, var(--accent), color-mix(in oklch, var(--accent) 60%, var(--low)));
    box-shadow: 0 4px 12px -4px var(--accent);
  }
  .name {
    display: grid;
    font-weight: 700;
    letter-spacing: -0.01em;
    line-height: 1.15;
  }
  .sub {
    font-size: 11px;
    font-weight: 500;
    color: var(--muted);
    letter-spacing: 0;
  }
  nav {
    display: flex;
    gap: 4px;
  }
  nav a {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 7px 12px;
    border-radius: 8px;
    color: var(--muted);
    font-weight: 500;
    text-decoration: none;
    transition:
      background-color 120ms,
      color 120ms;
  }
  nav a:hover {
    color: var(--text);
    background: var(--surface-2);
  }
  nav a[aria-current='page'] {
    color: var(--text);
    background: var(--surface);
    box-shadow:
      inset 0 0 0 1px var(--border),
      0 1px 2px oklch(0% 0 0 / 0.06);
  }
  .count {
    min-width: 20px;
    padding: 0 6px;
    border-radius: 99px;
    font-size: 11px;
    font-weight: 700;
    line-height: 18px;
    text-align: center;
    color: var(--accent-contrast);
    background: var(--high);
  }
  .tools {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .conn {
    --c: var(--idle);
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 4px 10px;
    border-radius: 99px;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--c);
    background: color-mix(in oklch, var(--c) 12%, transparent);
    cursor: default;
  }
  .conn-live {
    --c: var(--ok);
  }
  .conn-offline {
    --c: var(--danger);
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
  }
  .conn-live .dot {
    animation: pulse 2s var(--ease) infinite;
  }
  .conn-connecting .dot {
    animation: blink 1s steps(2) infinite;
  }
  @keyframes pulse {
    0% {
      box-shadow: 0 0 0 0 color-mix(in oklch, var(--c) 55%, transparent);
    }
    100% {
      box-shadow: 0 0 0 8px transparent;
    }
  }
  @keyframes blink {
    50% {
      opacity: 0.3;
    }
  }
  .icon-btn {
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border-radius: 9px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--muted);
    cursor: pointer;
  }
  .icon-btn:hover {
    color: var(--text);
    border-color: var(--border-strong);
  }

  @media (max-width: 760px) {
    .inner {
      flex-wrap: wrap;
      gap: 10px 12px;
      padding: 10px 16px;
    }
    .sub {
      display: none;
    }
    nav {
      order: 3;
      width: 100%;
    }
    nav a {
      flex: 1;
      justify-content: center;
    }
  }
</style>
