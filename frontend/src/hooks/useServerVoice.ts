/**
 * Keeps the orb in speaking while the server talks: reminders, schedule
 * notices, moments. See lib/server-voice.ts for why this is kept apart from
 * the tab's own playback.
 */

import { useEffect } from 'react';
import { apiFetch } from '../lib/api';
import {
  applyServerVoice,
  clearServerVoice,
  serverVoiceTimeoutMs,
  takeSseEvents,
  type ServerVoiceEvent,
} from '../lib/server-voice';
import { useAppStore } from '../lib/store';
import { voiceTrace } from '../lib/voice-trace';

const RETRY_MIN_MS = 2_000;
const RETRY_MAX_MS = 30_000;
/** The server sends a keepalive every 15 s; this long with no bytes at all
 * means the connection is dead even if it never said so. A tab loaded at
 * 10:49 on 23 September sat on such a connection for over ten minutes and
 * missed a reminder: its first attempt failed, and nothing noticed the one
 * after it was never answered. */
const SILENT_MS = 40_000;

export function useServerVoice(): void {
  useEffect(() => {
    let stopped = false;
    let controller: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    let retryMs = RETRY_MIN_MS;
    let lastByteAt = Date.now();
    const liveness = setInterval(() => {
      if (controller && Date.now() - lastByteAt > SILENT_MS) {
        voiceTrace('serverVoice.stalled', { silentMs: Date.now() - lastByteAt });
        controller.abort();
      }
    }, 5_000);

    const setSpeaking = (speaking: boolean) => {
      if (useAppStore.getState().serverSpeaking !== speaking) {
        useAppStore.getState().setServerSpeaking(speaking);
      }
    };

    const quiet = () => {
      clearTimeout(watchdog);
      clearServerVoice();
      setSpeaking(false);
    };

    const onEvent = (event: ServerVoiceEvent) => {
      voiceTrace('serverVoice.event', { speaking: Boolean(event.speaking), channel: String(event.channel ?? '') });
      clearTimeout(watchdog);
      const speaking = applyServerVoice(event);
      setSpeaking(speaking);
      // A stop that never arrives must not hold the orb in speaking.
      if (speaking) watchdog = setTimeout(quiet, serverVoiceTimeoutMs(event));
    };

    const connect = async () => {
      controller = new AbortController();
      lastByteAt = Date.now();
      try {
        const res = await apiFetch('/v1/presence/voice/stream', {
          signal: controller.signal,
          headers: { Accept: 'text/event-stream' },
        });
        if (!res.ok || !res.body) throw new Error(`voice stream ${res.status}`);
        voiceTrace('serverVoice.connected', {});
        retryMs = RETRY_MIN_MS;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          lastByteAt = Date.now();
          buffer += decoder.decode(value, { stream: true });
          const { events, rest } = takeSseEvents(buffer);
          buffer = rest;
          for (const e of events) onEvent(e as ServerVoiceEvent);
        }
        voiceTrace('serverVoice.closed', { why: 'ended' });
      } catch (err) {
        if (!stopped) voiceTrace('serverVoice.closed', { why: String(err).slice(0, 80) });
      }
      controller = null;
      // Whatever was playing when the stream went away, the orb cannot know
      // it is still playing.
      quiet();
      if (!stopped) {
        retryTimer = setTimeout(() => void connect(), retryMs);
        retryMs = Math.min(RETRY_MAX_MS, retryMs * 2);
      }
    };

    void connect();
    return () => {
      stopped = true;
      clearInterval(liveness);
      controller?.abort();
      clearTimeout(retryTimer);
      quiet();
    };
  }, []);
}
