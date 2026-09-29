// Every page load -- a manual refresh, app start, tray "Restart Sage", or the
// automatic reload after a code edit or server restart -- lands on Chat
// (user's choice, 2026-09-29). Without this, BrowserRouter reloads whatever
// page was open (Dashboard, Settings, ...). The store already starts a fresh
// empty chat on load, so only the path needs resetting. Runs before render,
// so the other page never flashes.
//
// Voice is the exception (user's choice, 2026-09-29): a reload there stays on
// Voice, with the same fresh chat. App start still opens Chat, because the
// splash page always loads '/'.
const STAYS = new Set(['/', '/voice']);

export function landOnChat(
  location: Pick<Location, 'pathname' | 'search' | 'hash'> = window.location,
  history: Pick<History, 'replaceState' | 'state'> = window.history,
): boolean {
  const path = STAYS.has(location.pathname) ? location.pathname : '/';
  if (path === location.pathname && !location.search && !location.hash) return false;
  history.replaceState(history.state, '', path);
  return true;
}
