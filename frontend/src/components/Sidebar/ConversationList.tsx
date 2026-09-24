import { useMemo, useState } from 'react';
import { ChevronRight, Pin, PinOff, Trash2 } from 'lucide-react';
import { useNavigate } from 'react-router';
import { useAppStore } from '../../lib/store';
import { groupConversations } from '../../lib/chat-groups';
import type { Conversation } from '../../types';

interface Props {
  searchQuery: string;
}

function formatRelativeTime(timestamp: number): string {
  const diff = Date.now() - timestamp;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(timestamp).toLocaleDateString();
}

function SectionLabel({ children }: { children: string }) {
  return (
    <div
      className="px-3 pt-3 pb-1 text-[10px] uppercase tracking-[0.12em]"
      style={{ color: 'var(--color-text-tertiary)' }}
    >
      {children}
    </div>
  );
}

export function ConversationList({ searchQuery }: Props) {
  const conversations = useAppStore((s) => s.conversations);
  const activeId = useAppStore((s) => s.activeId);
  // Which older clusters are open. The one holding the open chat starts
  // open, so selecting an old chat never makes it vanish from the list.
  const [opened, setOpened] = useState<Record<string, boolean>>({});

  const groups = useMemo(() => groupConversations(conversations, Date.now()), [conversations]);

  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    const found = conversations.filter((c) => c.title.toLowerCase().includes(q));
    if (found.length === 0) {
      return (
        <div className="px-3 py-8 text-center text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
          No matching chats
        </div>
      );
    }
    return (
      <div className="flex flex-col gap-0.5 py-1">
        {found.map((conv) => <ConversationRow key={conv.id} conv={conv} />)}
      </div>
    );
  }

  if (conversations.length === 0) {
    return (
      <div className="px-3 py-8 text-center text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
        No conversations yet
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5 pb-1">
      {groups.pinned.length > 0 && (
        <>
          <SectionLabel>Pinned</SectionLabel>
          {groups.pinned.map((conv) => <ConversationRow key={conv.id} conv={conv} />)}
        </>
      )}
      {groups.recent.length > 0 && (
        <>
          {groups.pinned.length > 0 && <SectionLabel>Recent</SectionLabel>}
          {groups.recent.map((conv) => <ConversationRow key={conv.id} conv={conv} />)}
        </>
      )}
      {groups.clusters.map((cluster) => {
        const holdsActive = cluster.conversations.some((c) => c.id === activeId);
        const open = opened[cluster.key] ?? holdsActive;
        return (
          <div key={cluster.key} className="flex flex-col gap-0.5">
            <button
              onClick={() => setOpened((o) => ({ ...o, [cluster.key]: !open }))}
              aria-expanded={open}
              className="mt-1 flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs cursor-pointer transition-colors"
              style={{ color: 'var(--color-text-secondary)' }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              <ChevronRight
                size={13}
                className="transition-transform"
                style={{ transform: open ? 'rotate(90deg)' : 'none' }}
              />
              <span className="flex-1 text-left">{cluster.label}</span>
              <span className="text-[10px]" style={{ color: 'var(--color-text-tertiary)' }}>
                {cluster.conversations.length}
              </span>
            </button>
            {open && cluster.conversations.map((conv) => <ConversationRow key={conv.id} conv={conv} />)}
          </div>
        );
      })}
    </div>
  );
}

function ConversationRow({ conv }: { conv: Conversation }) {
  const navigate = useNavigate();
  const activeId = useAppStore((s) => s.activeId);
  const streamingConversationId = useAppStore((s) =>
    s.streamState.isStreaming ? s.streamState.conversationId : null,
  );
  const selectConversation = useAppStore((s) => s.selectConversation);
  const deleteConversation = useAppStore((s) => s.deleteConversation);
  const togglePin = useAppStore((s) => s.togglePinConversation);
  const isActive = conv.id === activeId;
  const isStreaming = conv.id === streamingConversationId;
  const PinIcon = conv.pinned ? PinOff : Pin;

  return (
    <div
      className="group flex items-center rounded-lg cursor-pointer transition-colors"
      style={{
        background: isActive ? 'var(--color-bg-tertiary)' : 'transparent',
      }}
      onMouseEnter={(e) => {
        if (!isActive) e.currentTarget.style.background = 'var(--color-bg-secondary)';
      }}
      onMouseLeave={(e) => {
        if (!isActive) e.currentTarget.style.background = 'transparent';
      }}
    >
      <button
        onClick={() => {
          selectConversation(conv.id);
          navigate('/');
        }}
        className="flex-1 text-left px-3 py-2 min-w-0 cursor-pointer"
      >
        <div
          className="text-sm truncate flex items-center gap-1.5"
          style={{
            color: isActive ? 'var(--color-text)' : 'var(--color-text-secondary)',
            fontWeight: isActive ? 500 : 400,
          }}
        >
          {conv.pinned && <Pin size={11} style={{ color: 'var(--color-accent)', flexShrink: 0 }} />}
          <span className="truncate">{conv.title}</span>
        </div>
        <div className="text-[11px] mt-0.5" style={{ color: 'var(--color-text-tertiary)' }}>
          {formatRelativeTime(conv.updatedAt)}
        </div>
      </button>
      <button
        onClick={(e) => {
          e.stopPropagation();
          togglePin(conv.id);
        }}
        className="p-1.5 rounded opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
        style={{ color: 'var(--color-text-tertiary)' }}
        onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--color-accent)')}
        onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--color-text-tertiary)')}
        title={conv.pinned ? 'Unpin conversation' : 'Pin conversation'}
        aria-label={conv.pinned ? 'Unpin conversation' : 'Pin conversation'}
      >
        <PinIcon size={14} />
      </button>
      <button
        onClick={(e) => {
          e.stopPropagation();
          deleteConversation(conv.id);
        }}
        disabled={isStreaming}
        className="p-1.5 mr-1 rounded opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer disabled:cursor-not-allowed disabled:opacity-30"
        style={{ color: 'var(--color-text-tertiary)' }}
        onMouseEnter={(e) => {
          if (!isStreaming) e.currentTarget.style.color = 'var(--color-error)';
        }}
        onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--color-text-tertiary)')}
        title={
          isStreaming
            ? 'Stop generating before deleting this conversation'
            : 'Delete conversation'
        }
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}
