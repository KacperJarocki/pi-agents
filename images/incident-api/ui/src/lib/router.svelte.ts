// Minimal client-side routing between the two entry pages. Both `/` and
// `/live/` load the same bundle, so a reload on either works; in-app links
// use the History API and keep the live connection open.
export type Page = 'devices' | 'live';

const pageFor = (path: string): Page => (/^\/live(\/|$)/.test(path) ? 'live' : 'devices');
// Read-only lookups; reactivity comes from `route`, which sync() updates.
const deviceParam = (search: string) => new URLSearchParams(search).get('device');

export const route = $state({
  page: pageFor(location.pathname),
  device: deviceParam(location.search),
});

function sync() {
  route.page = pageFor(location.pathname);
  route.device = deviceParam(location.search);
}

window.addEventListener('popstate', sync);

export function navigate(href: string, { replace = false } = {}) {
  // eslint-disable-next-line svelte/prefer-svelte-reactivity
  const url = new URL(href, location.href);
  if (url.href === location.href) return;
  if (replace) history.replaceState(null, '', url);
  else history.pushState(null, '', url);
  sync();
  if (!url.searchParams.has('device')) window.scrollTo({ top: 0 });
}

/** onclick handler for <a> elements: plain left clicks stay in the app. */
export function link(event: MouseEvent) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
    return;
  const anchor = event.currentTarget as HTMLAnchorElement;
  event.preventDefault();
  navigate(anchor.href);
}

export function deviceHref(deviceKey: string): string {
  return `/?device=${encodeURIComponent(deviceKey)}`;
}
