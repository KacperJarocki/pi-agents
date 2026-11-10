// UI preferences kept in localStorage.
export type Theme = 'system' | 'light' | 'dark';

function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`pi-agents:${key}`);
    return raw == null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export const prefs = $state({
  theme: load<Theme>('theme', 'system'),
  showResolved: load('showResolved', true),
});

$effect.root(() => {
  $effect(() => {
    localStorage.setItem('pi-agents:theme', JSON.stringify(prefs.theme));
    localStorage.setItem('pi-agents:showResolved', JSON.stringify(prefs.showResolved));
    if (prefs.theme === 'system') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = prefs.theme;
  });
});
