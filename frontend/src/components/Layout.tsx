import { useEffect, useState } from 'react';
import { Outlet, useNavigate } from 'react-router';
import { ApprovalBell } from './ApprovalBell';
import { Sidebar } from './Sidebar/Sidebar';
import { SystemPulse } from './SystemPulse';
import { useAppStore } from '../lib/store';
import { PersistentComposer } from './Chat/PersistentComposer';
import { checkHealth, getApiKey } from '../lib/api';
import { useKeyboardOpen, useTouchLayout } from '../lib/touch-layout';
import { ChatsDrawer, TouchTabBar, TouchTopBar } from './Touch/TouchChrome';
import { PairScreen } from './Touch/PairScreen';
import { devicePaired, keyRefused } from '../lib/pairing';

export function Layout() {
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);

  useEffect(() => {
    const check = () => checkHealth().then(setApiReachable);
    check();
    const interval = setInterval(check, 30000);
    const onFocus = () => check();
    window.addEventListener('focus', onFocus);
    return () => {
      clearInterval(interval);
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  const navigate = useNavigate();
  const touch = useTouchLayout();
  const [chatsOpen, setChatsOpen] = useState(false);
  const keyboardUp = useKeyboardOpen();
  // A phone or iPad without a working key pairs first (M29). Until the check
  // answers, nothing that opens the microphone is mounted.
  const [pairing, setPairing] = useState<'checking' | 'needed' | 'ok'>(touch ? 'checking' : 'ok');
  useEffect(() => {
    if (!touch) return;
    let live = true;
    void keyRefused().then((refused) => {
      if (live) setPairing(refused || !devicePaired() ? 'needed' : 'ok');
    });
    return () => {
      live = false;
    };
  }, [touch]);

  const healthBanner = apiReachable === false && (
    <div
      className="flex items-center gap-3 px-4 py-2 text-sm shrink-0"
      style={{
        background: 'color-mix(in srgb, var(--color-error) 8%, transparent)',
        borderBottom: '1px solid color-mix(in srgb, var(--color-error) 15%, transparent)',
        color: 'var(--color-text)',
      }}
    >
      <span
        className="w-1.5 h-1.5 rounded-full shrink-0"
        style={{ background: 'var(--color-error)' }}
      />
      <span>Cannot reach Sage backend</span>
      <button
        onClick={() => navigate('/settings')}
        className="text-sm underline cursor-pointer ml-auto shrink-0"
        style={{ color: 'var(--color-accent)' }}
      >
        Change URL
      </button>
    </div>
  );

  if (touch && pairing !== 'ok') {
    if (pairing === 'checking') return <div className="h-full w-full" />;
    // Reloaded once paired, so every socket opens with the key.
    return <PairScreen again={Boolean(getApiKey())} onPaired={() => window.location.reload()} />;
  }

  if (touch) {
    // Phone and iPad (M29): top bar, page, message box, tabs. The sidebar is
    // replaced by the tabs and the chats drawer.
    return (
      <div className="touch-shell flex flex-col h-full w-full overflow-hidden relative">
        <div className="hud-backdrop" aria-hidden="true" />
        <SystemPulse apiReachable={apiReachable} />
        <ApprovalBell />
        <div className="relative z-10 flex flex-col flex-1 min-h-0">
          <TouchTopBar onOpenChats={() => setChatsOpen(true)} />
          {healthBanner}
          <main className="flex-1 flex flex-col min-w-0 min-h-0 relative overflow-hidden">
            <div className="flex-1 flex flex-col min-w-0 min-h-0 relative z-[2]">
              <Outlet />
            </div>
            <PersistentComposer />
          </main>
          {!keyboardUp && <TouchTabBar />}
        </div>
        <ChatsDrawer open={chatsOpen} onClose={() => setChatsOpen(false)} />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full w-full overflow-hidden relative" style={{ paddingTop: '3px' }}>
      <div className="hud-backdrop" aria-hidden="true" />
      <SystemPulse apiReachable={apiReachable} />
      <ApprovalBell />

      {/* Health check banner */}
      {healthBanner}

      <div className="flex flex-1 min-h-0 relative z-10">
        <Sidebar />
        {sidebarOpen && (
          <div
            className="fixed inset-0 z-20 bg-black/40 md:hidden"
            onClick={() => useAppStore.getState().setSidebarOpen(false)}
          />
        )}
        <main className="flex-1 flex flex-col min-w-0 h-full relative overflow-hidden" style={{ background: 'transparent' }}>
          <div className="flex-1 flex flex-col min-w-0 min-h-0 relative z-[2]">
            <Outlet />
          </div>
          {/* One message box for every page: it owns the microphone, so a
              page change must not remount it (lib/composer-slot.ts). */}
          <PersistentComposer />
        </main>
      </div>
    </div>
  );
}
