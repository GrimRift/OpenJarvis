import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import { ErrorBoundary } from './components/ErrorBoundary';
import { initApiBase } from './lib/api';
import { preloadChats } from './lib/chat-db';
import { initAnalytics } from './lib/analytics';
import { landOnChat } from './lib/landing';
import './index.css';

function applyTheme() {
  try {
    const raw = localStorage.getItem('openjarvis-settings');
    const settings = raw ? JSON.parse(raw) : {};
    const theme = settings.theme || 'system';
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.classList.remove('light');
    } else if (theme === 'light') {
      document.documentElement.classList.add('light');
      document.documentElement.classList.remove('dark');
    }
  } catch { /* use system default */ }
}

applyTheme();
landOnChat();

// Fetch the API base URL from the Tauri backend before rendering.
// This ensures JARVIS_PORT is defined in one place (the Rust backend).
// In non-Tauri environments this is a no-op.
// The chats are read from IndexedDB before App (and with it the store) is
// imported: the store reads them synchronously when it is created.
Promise.all([initApiBase().catch(() => {}), preloadChats().catch(() => {})]).then(async () => {
  const { default: App } = await import('./App');
  // Kick off analytics init in the background — it's never awaited so
  // a slow/failed identity fetch never delays UI render.
  void initAnalytics();

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <ErrorBoundary>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ErrorBoundary>
    </StrictMode>,
  );
});
