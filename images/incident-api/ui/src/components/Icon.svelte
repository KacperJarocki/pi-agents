<script lang="ts" module>
  // Stroke icons in the Lucide style (24×24 grid, 2px stroke).
  const circle = (cx: number, cy: number, r: number) =>
    `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;

  export const ICONS = {
    shield: [
      'M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z',
      'm9 12 2 2 4-4',
    ],
    activity: ['M22 12h-4l-3 9L9 3l-3 9H2'],
    alert: ['m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3', 'M12 9v4', 'M12 17h.01'],
    offline: [
      'M12 20h.01',
      'M8.5 16.43a5 5 0 0 1 7 0',
      'M5 12.86a10 10 0 0 1 5.17-2.69',
      'M19 12.86a10 10 0 0 0-2-1.52',
      'M2 8.82a15 15 0 0 1 4.18-2.64',
      'M22 8.82a15 15 0 0 0-11.29-3.76',
      'm2 2 20 20',
    ],
    search: [circle(11, 11, 8), 'm21 21-4.3-4.3'],
    x: ['M18 6 6 18', 'm6 6 12 12'],
    sun: [
      circle(12, 12, 4),
      'M12 2v2',
      'M12 20v2',
      'm4.93 4.93 1.41 1.41',
      'm17.66 17.66 1.41 1.41',
      'M2 12h2',
      'M20 12h2',
      'm6.34 17.66-1.41 1.41',
      'm19.07 4.93-1.41 1.41',
    ],
    moon: ['M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z'],
    monitor: ['M4 3h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z', 'M8 21h8', 'M12 17v4'],
    chevron: ['m9 18 6-6-6-6'],
    router: [
      'M4 14h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2z',
      'M6 18h.01',
      'M10 18h.01',
      'M15 10v4',
      'M17.84 7.17a4 4 0 0 0-5.66 0',
      'M20.66 4.34a8 8 0 0 0-11.31 0',
    ],
    radio: [
      'M4.9 19.1C1 15.2 1 8.8 4.9 4.9',
      'M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5',
      circle(12, 12, 2),
      'M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5',
      'M19.1 4.9C23 8.8 23 15.1 19.1 19',
    ],
    refresh: [
      'M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8',
      'M21 3v5h-5',
      'M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16',
      'M8 16H3v5',
    ],
    clock: [circle(12, 12, 10), 'M12 6v6l4 2'],
    info: [circle(12, 12, 10), 'M12 16v-4', 'M12 8h.01'],
    check: [circle(12, 12, 10), 'm9 12 2 2 4-4'],
    gauge: ['m12 14 4-4', 'M3.34 19a10 10 0 1 1 17.32 0'],
    bell: ['M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9', 'M10.3 21a1.94 1.94 0 0 0 3.4 0'],
    'bell-off': [
      'M8.7 3A6 6 0 0 1 18 8a21.3 21.3 0 0 0 .6 5',
      'M17 17H3s3-2 3-9a4.67 4.67 0 0 1 .3-1.7',
      'M10.3 21a1.94 1.94 0 0 0 3.4 0',
      'm2 2 20 20',
    ],
    network: ['M16 16h6v6h-6z', 'M2 16h6v6H2z', 'M9 2h6v6H9z', 'M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3', 'M12 12V8'],
  } as const;

  export type IconName = keyof typeof ICONS;
</script>

<script lang="ts">
  let { name, size = 16, label }: { name: IconName; size?: number; label?: string } = $props();
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="2"
  stroke-linecap="round"
  stroke-linejoin="round"
  role={label ? 'img' : undefined}
  aria-label={label}
  aria-hidden={label ? undefined : 'true'}
>
  {#each ICONS[name] as d (d)}<path {d} />{/each}
</svg>

<style>
  svg {
    flex: none;
    display: block;
  }
</style>
