<script lang="ts">
  import { devices } from '../lib/devices.svelte';
  import type { Device } from '../lib/types';
  import Icon from './Icon.svelte';

  // Block/unblock a device on the gateway. Blocking asks for confirmation
  // inline; unblocking is one click. Hidden behind DEVICE_BLOCKING on the API.
  let { device }: { device: Device } = $props();

  let confirming = $state(false);
  let busy = $state(false);
  let error = $state<string | null>(null);

  const name = $derived(device.hostname || device.mac);

  async function run(blocked: boolean) {
    busy = true;
    error = null;
    try {
      await devices.setBlocked(device.mac, blocked);
      confirming = false;
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      busy = false;
    }
  }
</script>

<section class="actions" data-testid="block-control">
  <h3>Network access</h3>
  {#if !devices.canBlock}
    <p class="muted">
      Blocking is turned off on this server. Enable <code>incidents.api.deviceBlocking</code> to block devices from here.
    </p>
  {:else if device.blocked}
    <div class="state blocked" role="status">
      <Icon name="x" size={16} />
      <div>
        <strong>Blocked</strong>
        <span class="muted">Traffic from this device is dropped at the gateway.</span>
      </div>
      <button class="btn" type="button" disabled={busy} onclick={() => run(false)}>Unblock</button>
    </div>
  {:else if confirming}
    <div class="confirm" role="alertdialog" aria-labelledby="block-q" aria-describedby="block-d">
      <strong id="block-q">Block {name}?</strong>
      <p id="block-d" class="muted">
        The gateway drops everything it sends until you unblock it here, or until the gateway's firewall is reset.
      </p>
      <div class="buttons">
        <button class="btn" type="button" disabled={busy} onclick={() => (confirming = false)}>Cancel</button>
        <button class="btn danger" type="button" disabled={busy} onclick={() => run(true)}>
          {busy ? 'Blocking…' : 'Block device'}
        </button>
      </div>
    </div>
  {:else}
    <div class="state">
      <Icon name="check" size={16} />
      <div>
        <strong>Allowed</strong>
        <span class="muted">Cut the device off if it should not be doing this.</span>
      </div>
      <button class="btn danger-outline" type="button" onclick={() => (confirming = true)}>Block…</button>
    </div>
  {/if}
  {#if error}<p class="error" role="alert">Could not change the block: {error}</p>{/if}
</section>

<style>
  .actions {
    margin-bottom: 24px;
  }
  h3 {
    font-size: 15px;
    margin-bottom: 8px;
  }
  p {
    margin: 0;
  }
  code {
    font-family: var(--mono);
    font-size: 12px;
  }
  .state,
  .confirm {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface-2);
  }
  .state > div {
    flex: 1;
    display: grid;
  }
  .state > :global(svg) {
    color: var(--ok);
  }
  .state.blocked {
    border-color: color-mix(in oklch, var(--high) 40%, transparent);
    background: color-mix(in oklch, var(--high) 8%, var(--surface));
  }
  .state.blocked > :global(svg) {
    color: var(--high);
  }
  .confirm {
    display: grid;
    gap: 6px;
    border-color: color-mix(in oklch, var(--high) 40%, transparent);
  }
  .buttons {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 4px;
  }
  .danger {
    color: var(--accent-contrast);
    background: var(--high);
    border-color: transparent;
  }
  .danger:hover {
    background: color-mix(in oklch, var(--high) 85%, black);
  }
  .danger-outline {
    color: var(--high);
    border-color: color-mix(in oklch, var(--high) 45%, transparent);
  }
  .btn:disabled {
    opacity: 0.6;
    cursor: progress;
  }
  .error {
    margin-top: 8px;
    color: var(--danger);
  }
</style>
