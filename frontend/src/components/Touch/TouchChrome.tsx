/**
 * The touch layout's frame (M29, approved mockup "picks-v2"): a top bar with
 * the SAGE wordmark, a bottom tab bar, and a drawer holding the chats.
 * Pages beyond the four tabs live under More.
 */
import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import {
  Activity,
  AudioLines,
  BarChart3,
  BookOpen,
  Bot,
  Brain,
  ChevronRight,
  Database,
  Menu,
  MessageSquare,
  MoreHorizontal,
  Search,
  ScrollText,
  Settings,
  SquarePen,
} from 'lucide-react';
import { ConversationList } from '../Sidebar/ConversationList';
import { OrbVisual, useOrbState } from '../Chat/OrbVisual';
import { useAppStore } from '../../lib/store';

const TABS = [
  { path: '/', icon: MessageSquare, label: 'Chat' },
  { path: '/voice', icon: AudioLines, label: 'Voice' },
  { path: '/books', icon: BookOpen, label: 'Books' },
  { path: '/memory', icon: Brain, label: 'Memory' },
  { path: '/more', icon: MoreHorizontal, label: 'More' },
];

/** Everything that is not a tab, in the desktop sidebar's order. */
const MORE_PAGES = [
  { path: '/dashboard', icon: BarChart3, label: 'Dashboard' },
  { path: '/system-health', icon: Activity, label: 'Health' },
  { path: '/data-sources', icon: Database, label: 'Data Sources' },
  { path: '/agents', icon: Bot, label: 'Agents' },
  { path: '/logs', icon: ScrollText, label: 'Logs' },
  { path: '/settings', icon: Settings, label: 'Settings' },
];

const TITLES: Record<string, string> = Object.fromEntries(
  [...TABS, ...MORE_PAGES].map((page) => [page.path, page.label]),
);

/** The tab a page belongs to: a page reached from More lights More. */
function tabFor(pathname: string): string {
  if (TABS.some((tab) => tab.path === pathname)) return pathname;
  return '/more';
}

export function TouchTabBar() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const active = tabFor(pathname);
  return (
    <nav
      className="touch-tabbar shrink-0 flex justify-around"
      style={{
        borderTop: '1px solid var(--color-border-subtle)',
        background: 'color-mix(in srgb, var(--color-bg) 88%, transparent)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
      }}
    >
      {TABS.map((tab) => {
        const on = tab.path === active;
        return (
          <button
            key={tab.path}
            type="button"
            onClick={() => navigate(tab.path)}
            aria-current={on ? 'page' : undefined}
            className="flex flex-col items-center gap-0.5 pt-1.5 w-16 text-[10px] cursor-pointer"
            style={{ color: on ? 'var(--color-accent)' : 'var(--color-text-tertiary)' }}
          >
            <tab.icon
              size={22}
              strokeWidth={1.8}
              style={on ? { filter: 'drop-shadow(0 0 5px var(--color-accent-glow))' } : undefined}
            />
            {tab.label}
          </button>
        );
      })}
    </nav>
  );
}

function useNewChat() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  return () => {
    const store = useAppStore.getState();
    // As the desktop sidebar: an empty chat is not replaced by another.
    if (store.messages.length > 0) store.createConversation(store.selectedModel);
    const target = pathname === '/voice' ? '/voice' : '/';
    if (pathname !== target) navigate(target);
  };
}

const iconButton = 'w-10 h-10 grid place-items-center rounded-full cursor-pointer';

export function TouchTopBar({ onOpenChats }: { onOpenChats: () => void }) {
  const { pathname } = useLocation();
  const newChat = useNewChat();
  const selectedModel = useAppStore((s) => s.selectedModel);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const deepResearch = useAppStore((s) => s.deepResearch);
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen);
  const isChat = pathname === '/';
  const isVoice = pathname === '/voice';
  // Once a chat has begun the big orb steps aside, and a small one sits
  // beside the name in its place (M29, the user's request).
  const chatting = useAppStore((s) => s.messages.length > 0);
  const orbState = useOrbState();
  const title = TITLES[pathname] ?? 'More';

  return (
    <header
      className="shrink-0 flex items-center gap-1.5 px-2 h-[52px]"
      style={{ borderBottom: isVoice ? 'none' : '1px solid var(--color-border-subtle)' }}
    >
      {isChat ? (
        <button type="button" className={iconButton} onClick={onOpenChats} aria-label="Your chats"
          style={{ color: 'var(--color-text-secondary)' }}>
          <Menu size={21} />
        </button>
      ) : (
        <span className="w-10" />
      )}
      <div className="flex-1 min-w-0 flex flex-col items-center">
        {isChat || isVoice ? (
          // SAGE stays centred (the user's ask); the orb hangs off its left.
          <span className="relative inline-flex items-center">
            {isChat && chatting && (
              <span className="absolute right-full mr-2.5 top-1/2 -translate-y-1/2" aria-hidden="true">
                <OrbVisual state={orbState} size={34} pixelRatio={3} />
              </span>
            )}
            <span className="sage-wordmark select-none" aria-label="Sage">SAGE</span>
          </span>
        ) : (
          <span className="text-[15px] font-semibold truncate" style={{ color: 'var(--color-text)' }}>{title}</span>
        )}
        {isChat && (
          // The model under the name; a tap picks another (the desktop's ⌘K).
          <button
            type="button"
            onClick={() => setCommandPaletteOpen(true)}
            className="flex items-center gap-1.5 text-[11px] cursor-pointer max-w-[60vw]"
            style={{ color: deepResearch ? 'var(--color-accent)' : 'var(--color-text-tertiary)' }}
          >
            <span className="w-1.5 h-1.5 rounded-full shrink-0"
              style={{ background: 'var(--color-success)', boxShadow: '0 0 6px var(--color-success)' }} />
            <span className="truncate">
              {deepResearch ? 'Deep Research' : selectedModel || serverInfo?.model || 'Pick a model'}
            </span>
          </button>
        )}
      </div>
      {isChat || isVoice ? (
        <button type="button" className={iconButton} onClick={newChat} aria-label="New chat"
          style={{ color: 'var(--color-text-secondary)' }}>
          <SquarePen size={19} />
        </button>
      ) : (
        <span className="w-10" />
      )}
    </header>
  );
}

/** ☰ in Chat: the chats only, since the pages are on the tabs. */
export function ChatsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const newChat = useNewChat();
  return (
    <>
      <div
        className="fixed inset-0 z-40 transition-opacity duration-200"
        style={{ background: 'rgba(0,0,0,0.45)', opacity: open ? 1 : 0, pointerEvents: open ? 'auto' : 'none' }}
        onClick={onClose}
      />
      <aside
        className="touch-drawer fixed top-0 bottom-0 left-0 z-50 flex flex-col transition-transform duration-200 ease-out"
        style={{
          width: 'min(300px, 82vw)',
          transform: open ? 'translateX(0)' : 'translateX(-102%)',
          background: 'var(--color-bg)',
          borderRight: '1px solid var(--color-border)',
        }}
        aria-hidden={!open}
      >
        <div className="flex items-center justify-between px-4 pb-3">
          <span className="sage-wordmark select-none">SAGE</span>
          <button
            type="button"
            onClick={() => { newChat(); onClose(); }}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[13px] cursor-pointer"
            style={{ border: '1px solid var(--color-accent)', color: 'var(--color-accent)' }}
          >
            <SquarePen size={14} /> New chat
          </button>
        </div>
        <div className="px-3 mb-2">
          <div className="flex items-center gap-2 px-3 py-2 rounded-xl"
            style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
            <Search size={15} style={{ color: 'var(--color-text-tertiary)' }} />
            <input
              type="text"
              placeholder="Search chats"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="flex-1 bg-transparent outline-none text-[16px]"
              style={{ color: 'var(--color-text)' }}
            />
          </div>
        </div>
        {/* Picking a chat closes the drawer. */}
        <div className="flex-1 overflow-y-auto px-2" onClick={(e) => {
          if ((e.target as HTMLElement).closest('[data-conversation-open]')) onClose();
        }}>
          <ConversationList searchQuery={query} />
        </div>
      </aside>
    </>
  );
}

/** The More tab: the pages that are not tabs. */
export function MorePage() {
  const navigate = useNavigate();
  return (
    <div className="flex-1 overflow-y-auto px-4 py-2">
      {MORE_PAGES.map((page) => (
        <button
          key={page.path}
          type="button"
          onClick={() => navigate(page.path)}
          className="w-full flex items-center gap-3 px-1 py-3.5 text-[15px] cursor-pointer"
          style={{ borderBottom: '1px solid var(--color-border-subtle)', color: 'var(--color-text)' }}
        >
          <page.icon size={19} style={{ color: 'var(--color-accent)' }} />
          {page.label}
          <ChevronRight size={17} className="ml-auto" style={{ color: 'var(--color-text-tertiary)' }} />
        </button>
      ))}
    </div>
  );
}
