import { ChatArea } from '../components/Chat/ChatArea';
import { useAppStore } from '../lib/store';

// The chat column centers itself (mx-auto) within whatever width this page
// gives it. Without this, that's "window width minus the sidebar" — which
// visually centers the column in the wrong place once there's nothing on
// the right to balance the sidebar's width. Mirroring the sidebar's width
// here as a phantom right-side gutter makes the centered column's midpoint
// land on the true window center instead.
//
// Gated to md: and up to match Sidebar.tsx's own `fixed md:relative` split —
// below that breakpoint the sidebar is a fixed overlay that consumes no
// layout width at all, so adding a phantom gutter there would overcompensate
// and push the column off-center the other way.

export function ChatPage() {
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);

  return (
    <div className="flex h-full overflow-hidden">
      <div
        className={`flex-1 min-w-0 transition-[padding] duration-200 ease-in-out ${sidebarOpen ? 'md:pr-[260px]' : ''}`}
      >
        <ChatArea />
      </div>
    </div>
  );
}
