// A shared "now" that ticks once a second so relative times stay current.
export const clock = $state({ now: Date.now() });

if (typeof window !== 'undefined') {
  setInterval(() => (clock.now = Date.now()), 1000);
}
