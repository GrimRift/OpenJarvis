import { useRef, useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router';
import { MessageBubble } from './MessageBubble';
import { InputArea } from './InputArea';
import { StreamingDots } from './StreamingDots';
import { OrbVisual, useOrbState } from './OrbVisual';
import { useAppStore } from '../../lib/store';
import { Database, MessageSquare, X } from 'lucide-react';
import { listConnectors } from '../../lib/connectors-api';

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

export function ChatArea() {
  const activeId = useAppStore((s) => s.activeId);
  const messages = useAppStore((s) => s.messages);
  const streamState = useAppStore((s) => s.streamState);
  const orbState = useOrbState();
  const navigate = useNavigate();
  const listRef = useRef<HTMLDivElement>(null);
  const shouldAutoScroll = useRef(true);
  const wasStreaming = useRef(false);
  const lastScrollTop = useRef(0);
  const isCurrentChatStreaming = streamState.isStreaming && streamState.conversationId === activeId;
  const currentStreamContent = isCurrentChatStreaming ? streamState.content : '';
  const orbStateLabel = orbState === 'listening' ? 'Listening' : orbState === 'speaking' ? 'Speaking' : 'Standing by';

  // Check if any data sources are connected
  const [hasConnectedSources, setHasConnectedSources] = useState<boolean | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  useEffect(() => {
    listConnectors()
      .then((list) => setHasConnectedSources(list.some((c) => c.connected)))
      .catch(() => setHasConnectedSources(null));
  }, []);

  useEffect(() => {
    // Sending a message always pins the view to the bottom, even if the
    // user had scrolled up to read earlier messages.
    if (isCurrentChatStreaming && !wasStreaming.current) {
      shouldAutoScroll.current = true;
    }
    wasStreaming.current = isCurrentChatStreaming;
    if (shouldAutoScroll.current && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, currentStreamContent, isCurrentChatStreaming]);

  const handleScroll = () => {
    if (!listRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = listRef.current;
    const distance = scrollHeight - scrollTop - clientHeight;
    const scrolledUp = scrollTop < lastScrollTop.current;
    lastScrollTop.current = scrollTop;
    if (scrolledUp && distance >= 1) {
      // Any upward scroll away from the bottom stops autoscroll immediately,
      // so streaming content never fights the user (no jitter). Sub-1px
      // upward movement (elastic bounce settling at the bottom) is ignored.
      shouldAutoScroll.current = false;
    } else if (!scrolledUp) {
      // Re-engage when scrolled back to the bottom. < 2 rather than < 1:
      // at fractional zoom levels the at-bottom residual can reach 1px,
      // which would otherwise leave autoscroll permanently disengaged.
      shouldAutoScroll.current = distance < 2;
    }
  };

  const isEmpty = messages.length === 0 && !isCurrentChatStreaming;

  return (
    <div className="flex flex-col h-full">
      {/* Data sources banner */}
      {hasConnectedSources === false && !bannerDismissed && (
        <div
          className="mx-4 mb-2 flex items-center gap-3 px-4 py-3 rounded-lg text-sm shrink-0"
          style={{
            background: 'var(--color-accent-subtle)',
            border: '1px solid var(--color-border)',
          }}
        >
          <Database size={16} style={{ color: 'var(--color-accent)', flexShrink: 0 }} />
          <span style={{ color: 'var(--color-text-secondary)', flex: 1 }}>
            Connect your data sources (Gmail, iMessage, Slack, etc.) to get personalized answers.
          </span>
          <button
            onClick={() => navigate('/data-sources')}
            className="px-3 py-1 rounded text-xs font-medium cursor-pointer"
            style={{ background: 'var(--color-accent)', color: 'var(--color-on-accent)', border: 'none' }}
          >
            Connect
          </button>
          <button
            onClick={() => setBannerDismissed(true)}
            className="p-1 rounded cursor-pointer"
            style={{ color: 'var(--color-text-tertiary)', background: 'transparent', border: 'none' }}
          >
            <X size={14} />
          </button>
        </div>
      )}
      {/* Capped at chat-max-width and centered, same as the composer below —
          so the scroll area (and its scrollbar) hug the actual readable
          column instead of spanning the full ChatArea width. */}
      <div className="relative flex-1 min-h-0 w-full max-w-[var(--chat-max-width)] mx-auto">
      {/* overflow-x-hidden is load-bearing, not tidying. `overflow-y-auto`
          alone leaves the x axis `visible`, which CSS promotes to `auto` the
          moment the other axis is not visible -- so one long unbroken URL in
          a reply gave the whole column a horizontal scrollbar (measured:
          scrollWidth 720 -> 1054), and scrolling it clipped the first
          characters off every message. Long text now wraps instead (see
          `.prose` in index.css); code blocks and tables keep their own
          internal scroll, which is where sideways scrolling belongs. */}
      <div
        ref={listRef}
        onScroll={handleScroll}
        className="h-full overflow-y-auto overflow-x-hidden"
      >
        {isEmpty ? (
          <div className="flex flex-col items-center justify-center h-full px-4">
            <div
              className="text-[10px] uppercase mb-2"
              style={{ color: 'var(--color-text-tertiary)', letterSpacing: '0.05em' }}
            >
              {orbStateLabel}
            </div>
            <OrbVisual state={orbState} />
            <h2
              className="font-semibold mt-1"
              style={{ color: 'var(--color-text)', fontFamily: 'var(--font-display)', fontSize: 26 }}
            >
              {getGreeting()}
            </h2>
            <p className="text-sm text-center max-w-sm mt-2 mb-6" style={{ color: 'var(--color-text-secondary)' }}>
              Ask anything. Your AI runs locally — private, fast, and always available.
            </p>

            {/* Quick action hints */}
            <div className="flex gap-2 flex-wrap justify-center">
              <button
                onClick={() => navigate('/data-sources')}
                className="flex items-center gap-2 px-3.5 py-2 rounded-full text-xs cursor-pointer transition-colors"
                style={{
                  background: 'transparent',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-text-secondary)',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-accent)')}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
              >
                <Database size={14} style={{ color: 'var(--color-accent)' }} />
                Connect Data Sources
              </button>
              <button
                onClick={() => { navigate('/data-sources'); setTimeout(() => window.dispatchEvent(new CustomEvent('switch-tab', { detail: 'messaging' })), 100); }}
                className="flex items-center gap-2 px-3.5 py-2 rounded-full text-xs cursor-pointer transition-colors"
                style={{
                  background: 'transparent',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-text-secondary)',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-accent)')}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
              >
                <MessageSquare size={14} style={{ color: 'var(--color-accent)' }} />
                Set Up Messaging Channels
              </button>
            </div>
          </div>
        ) : (
          <div className="px-4 py-6">
            {messages.map((msg, i) => {
              const isLastAssistant =
                i === messages.length - 1 && msg.role === 'assistant';
              const displayedMessage =
                isLastAssistant && isCurrentChatStreaming && currentStreamContent
                  ? { ...msg, content: currentStreamContent }
                  : msg;
              return (
                <MessageBubble
                  key={msg.id}
                  message={displayedMessage}
                  isLive={isLastAssistant && isCurrentChatStreaming}
                />
              );
            })}
            {(() => {
              if (!isCurrentChatStreaming || streamState.content !== '') return null;
              // For research messages the ResearchTimeline handles its own
              // pre-content loading state — suppress the generic dots.
              const last = messages[messages.length - 1];
              if (last?.role === 'assistant' && last.isResearch) return null;
              return (
                <div className="flex justify-start mb-4">
                  <StreamingDots phase={streamState.phase} />
                </div>
              );
            })()}
          </div>
        )}
      </div>
      </div>
      {!isEmpty && (
        <div style={{ position: 'fixed', bottom: 24, right: 24, pointerEvents: 'none', zIndex: 5 }}>
          <OrbVisual state={orbState} />
        </div>
      )}
      <InputArea />
    </div>
  );
}
