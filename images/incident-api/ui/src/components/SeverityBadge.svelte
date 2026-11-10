<script lang="ts">
  import { titleCase } from '../lib/format';
  import Badge, { type Tone } from './Badge.svelte';

  let { severity, muted = false }: { severity: string; muted?: boolean } = $props();
  const tone = $derived<Tone>(
    muted ? 'idle' : (({ HIGH: 'high', MEDIUM: 'medium', LOW: 'low' } as Record<string, Tone>)[severity] ?? 'neutral'),
  );
</script>

<Badge {tone} dot testid="severity" title={severity}>{titleCase(severity)}</Badge>
