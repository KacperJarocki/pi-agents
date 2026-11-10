<script lang="ts">
  import AppHeader from './components/AppHeader.svelte';
  import DevicesPage from './components/DevicesPage.svelte';
  import LivePage from './components/LivePage.svelte';
  import { devices } from './lib/devices.svelte';
  import { live } from './lib/live.svelte';
  import './lib/prefs.svelte';
  import { route } from './lib/router.svelte';

  // Both views share one live connection and one device poll, so switching
  // tabs is instant and the header always shows the connection state.
  live.start();
  devices.start();

  // A live incident change also changes device state and counts.
  $effect(() => {
    if (live.changes > 0) devices.refresh();
  });

  const active = $derived(live.active.length);
  $effect(() => {
    const base = route.page === 'live' ? 'Live incidents' : 'Devices';
    document.title = active > 0 ? `(${active}) ${base} · pi-agents` : `${base} · pi-agents`;
  });
</script>

<AppHeader />
<main>
  {#if route.page === 'live'}
    <LivePage />
  {:else}
    <DevicesPage />
  {/if}
</main>

<style>
  main {
    max-width: 1240px;
    margin: 0 auto;
    padding: 28px 24px 64px;
  }
  @media (max-width: 760px) {
    main {
      padding: 20px 16px 48px;
    }
  }
</style>
