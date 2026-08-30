import { useCallback, useEffect, useRef } from 'react';
import { useAppStore } from '../lib/store';
import {
  chunkDuration,
  decodePcmF32,
  interpretTtsMessage,
  nextStartTime,
  outputTailDelayMs,
  PlaybackGeneration,
} from '../lib/pcm-playback';
import { analyseInto } from '../lib/speech-analyser';
import type { VoiceProfile } from '../lib/voice-profiles';

let nextPlaybackOwner = 0;

/**
 * Speak a reply by streaming PCM from the server and scheduling it into Web
 * Audio as it arrives.
 *
 * The batch endpoint returns nothing until the whole clip exists — 1.55s for a
 * one-line reply, 6.92s for a paragraph, all silence. Streaming starts the
 * voice at about 0.41s.
 *
 * `speak()` resolves false when nothing was spoken, so the caller can fall back
 * to the batch endpoint. It resolves true once audio has started, because a
 * failure after that point cannot be retried without repeating the opening.
 */
export function useStreamingTts() {
  const playbackOwnerRef = useRef('');
  if (!playbackOwnerRef.current) {
    playbackOwnerRef.current = `streaming-tts-${++nextPlaybackOwner}`;
  }
  const socketRef = useRef<WebSocket | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const scheduledUntilRef = useRef(0);
  const sampleRateRef = useRef(24000);
  const sourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const completionTimerRef = useRef<number | null>(null);
  const generationsRef = useRef(new PlaybackGeneration());
  // Every scheduled chunk feeds this, so the orb reacts to the real waveform
  // instead of a sine wave pretending to be speech.
  const gainRef = useRef<GainNode | null>(null);
  const stopAnalyserRef = useRef<(() => void) | null>(null);

  const teardown = useCallback(() => {
    generationsRef.current.cancel();
    if (completionTimerRef.current !== null) {
      window.clearTimeout(completionTimerRef.current);
      completionTimerRef.current = null;
    }
    for (const src of sourcesRef.current) {
      try {
        src.stop();
      } catch {
        // Already finished; nothing to stop.
      }
    }
    sourcesRef.current = [];
    stopAnalyserRef.current?.();
    stopAnalyserRef.current = null;
    gainRef.current = null;
    try {
      socketRef.current?.close();
    } catch {
      // Already closing.
    }
    socketRef.current = null;
    scheduledUntilRef.current = 0;
    // The orb and the top pulse both read this, and the wake-word listener
    // stays suspended while it is true. Leaving it set after a manual stop
    // would freeze the orb mid-speech and keep the mic disarmed.
    useAppStore
      .getState()
      .setAudioPlayback(playbackOwnerRef.current, false);
  }, []);

  useEffect(
    () => () => {
      teardown();
      try {
        void ctxRef.current?.close();
      } catch {
        // Already closed.
      }
      ctxRef.current = null;
    },
    [teardown],
  );

  const speak = useCallback(
    (text: string, voice: VoiceProfile): Promise<boolean> =>
      new Promise<boolean>((resolve) => {
        if (!text.trim()) {
          resolve(false);
          return;
        }

        // A new utterance owns the one streaming channel. Invalidate every
        // callback and timer from the previous one before claiming playback.
        teardown();
        const generation = generationsRef.current.begin();
        const owner = playbackOwnerRef.current;
        useAppStore.getState().setAudioPlayback(owner, true);

        let settled = false;
        let started = false;
        let streamComplete = false;
        const activeSources = new Set<AudioBufferSourceNode>();
        const finish = (spoke: boolean) => {
          if (settled) return;
          settled = true;
          resolve(spoke);
        };

        // Reused for the hook's lifetime. Building one per reply orphaned the
        // previous context, and browsers cap how many can exist at once — so
        // after a handful of replies construction threw, and every reply after
        // that silently fell back to the batch player.
        let ctx: AudioContext;
        try {
          const existing = ctxRef.current;
          if (existing && existing.state !== 'closed') {
            ctx = existing;
          } else {
            const Ctor =
              window.AudioContext ||
              (window as unknown as { webkitAudioContext: typeof AudioContext })
                .webkitAudioContext;
            // Matching Cartesia's rate avoids resampling every chunk.
            ctx = new Ctor({ sampleRate: 24000 });
          }
        } catch {
          useAppStore.getState().setAudioPlayback(owner, false);
          finish(false);
          return;
        }
        ctxRef.current = ctx;
        stopAnalyserRef.current?.();
        const gain = ctx.createGain();
        gainRef.current = gain;
        try {
          stopAnalyserRef.current = analyseInto(ctx, gain);
        } catch {
          // Analysis is decoration; never let it cost the reply.
          gain.connect(ctx.destination);
        }

        const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
        let socket: WebSocket;
        try {
          socket = new WebSocket(
            `${proto}://${window.location.host}/v1/speech/tts-stream`,
          );
        } catch {
          useAppStore.getState().setAudioPlayback(owner, false);
          finish(false);
          return;
        }
        socket.binaryType = 'arraybuffer';
        socketRef.current = socket;

        const setPlaying = (playing: boolean) => {
          if (!generationsRef.current.isCurrent(generation)) return;
          useAppStore.getState().setAudioPlayback(owner, playing);
        };

        // A fresh AudioContext starts suspended under the browser's autoplay
        // policy. Without this the buffers below are scheduled and simply
        // never heard.
        socket.onopen = () => {
          if (!generationsRef.current.isCurrent(generation)) {
            socket.close();
            return;
          }
          void ctx.resume().catch(() => {});
          socket.send(JSON.stringify({
            text,
            voice_id: voice.id,
            speed: voice.speed,
            volume: voice.volume,
          }));
        };

        // The server's `done` means all buffers were delivered, not that the
        // last buffer has reached the speakers. Wait for its real `onended`
        // event, then include the host device's output latency. The generation
        // guard prevents an older utterance from clearing a newer one's claim.
        const settlePlaying = () => {
          if (
            !streamComplete ||
            activeSources.size > 0 ||
            !generationsRef.current.isCurrent(generation)
          ) {
            return;
          }
          if (completionTimerRef.current !== null) {
            window.clearTimeout(completionTimerRef.current);
          }
          const outputLatency =
            (ctx as AudioContext & { outputLatency?: number }).outputLatency ?? 0;
          completionTimerRef.current = window.setTimeout(() => {
            completionTimerRef.current = null;
            setPlaying(false);
          }, outputTailDelayMs(ctx.baseLatency, outputLatency));
        };

        socket.onmessage = (event) => {
          if (!generationsRef.current.isCurrent(generation)) return;
          if (typeof event.data === 'string') {
            const msg = interpretTtsMessage(event.data);
            if (msg.kind === 'start') {
              if (ctx.state !== 'running') {
                // Resume was refused — no user gesture yet. Nothing would be
                // audible, so report "not spoken" and let the caller fall
                // back to the <audio> element, which has its own autoplay
                // handling. Silence here used to look like a hung reply.
                finish(false);
                setPlaying(false);
                socket.close();
                return;
              }
              sampleRateRef.current = msg.sampleRate;
              scheduledUntilRef.current = 0;
              started = true;
              // Claimed before the first buffer is scheduled: the wake-word
              // listener re-arms the moment streaming ends, and without this
              // it could hear Sage's own reply.
              setPlaying(true);
              finish(true);
            } else if (msg.kind === 'error') {
              // Recoverable only before audio began.
              if (!msg.started && !started) {
                finish(false);
                setPlaying(false);
              } else {
                finish(true);
                streamComplete = true;
                settlePlaying();
              }
            } else if (msg.kind === 'done') {
              streamComplete = true;
              settlePlaying();
              finish(true);
            }
            return;
          }

          if (!started || !generationsRef.current.isCurrent(generation)) return;
          const samples = decodePcmF32(event.data as ArrayBuffer);
          if (samples.length === 0) return;

          const rate = sampleRateRef.current;
          const buffer = ctx.createBuffer(1, samples.length, rate);
          buffer.copyToChannel(samples, 0);
          const source = ctx.createBufferSource();
          source.buffer = buffer;
          source.connect(gainRef.current ?? ctx.destination);

          const startAt = nextStartTime(ctx.currentTime, scheduledUntilRef.current);
          source.start(startAt);
          scheduledUntilRef.current =
            startAt + chunkDuration(samples.length, rate);

          sourcesRef.current.push(source);
          activeSources.add(source);
          source.onended = () => {
            sourcesRef.current = sourcesRef.current.filter((s) => s !== source);
            activeSources.delete(source);
            settlePlaying();
          };
        };

        socket.onerror = () => {
          finish(started);
          if (started) {
            streamComplete = true;
            settlePlaying();
          }
          else setPlaying(false);
        };
        socket.onclose = () => {
          // Also on the started path: a socket that drops mid-reply used to
          // leave the orb speaking forever.
          if (started) {
            streamComplete = true;
            settlePlaying();
          }
          else setPlaying(false);
          finish(started);
        };
      }),
    [teardown],
  );

  return { speak, stop: teardown };
}
