import { useCallback, useEffect, useRef } from 'react';
import { useAppStore } from '../lib/store';
import {
  chunkDuration,
  decodePcmF32,
  interpretTtsMessage,
  nextStartTime,
} from '../lib/pcm-playback';
import { analyseInto } from '../lib/speech-analyser';

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
  const socketRef = useRef<WebSocket | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const scheduledUntilRef = useRef(0);
  const sampleRateRef = useRef(24000);
  const sourcesRef = useRef<AudioBufferSourceNode[]>([]);
  // Every scheduled chunk feeds this, so the orb reacts to the real waveform
  // instead of a sine wave pretending to be speech.
  const gainRef = useRef<GainNode | null>(null);
  const stopAnalyserRef = useRef<(() => void) | null>(null);

  const teardown = useCallback(() => {
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
    try {
      void ctxRef.current?.close();
    } catch {
      // Already closed.
    }
    ctxRef.current = null;
    scheduledUntilRef.current = 0;
    // The orb and the top pulse both read this, and the wake-word listener
    // stays suspended while it is true. Leaving it set after a manual stop
    // would freeze the orb mid-speech and keep the mic disarmed.
    useAppStore.getState().setAudioPlaying(false);
  }, []);

  useEffect(() => teardown, [teardown]);

  const speak = useCallback(
    (text: string): Promise<boolean> =>
      new Promise<boolean>((resolve) => {
        if (!text.trim()) {
          resolve(false);
          return;
        }

        let settled = false;
        let started = false;
        const finish = (spoke: boolean) => {
          if (settled) return;
          settled = true;
          resolve(spoke);
        };

        let ctx: AudioContext;
        try {
          const Ctor =
            window.AudioContext ||
            (window as unknown as { webkitAudioContext: typeof AudioContext })
              .webkitAudioContext;
          // Matching Cartesia's rate avoids resampling every chunk.
          ctx = new Ctor({ sampleRate: 24000 });
        } catch {
          finish(false);
          return;
        }
        ctxRef.current = ctx;
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
          finish(false);
          return;
        }
        socket.binaryType = 'arraybuffer';
        socketRef.current = socket;

        const setPlaying = (playing: boolean) =>
          useAppStore.getState().setAudioPlaying(playing);

        socket.onopen = () => socket.send(JSON.stringify({ text }));

        socket.onmessage = (event) => {
          if (typeof event.data === 'string') {
            const msg = interpretTtsMessage(event.data);
            if (msg.kind === 'start') {
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
              if (!msg.started && !started) finish(false);
              else finish(true);
            } else if (msg.kind === 'done') {
              // Let queued audio finish; the last source clears the flag.
              const remaining = Math.max(
                0,
                scheduledUntilRef.current - ctx.currentTime,
              );
              window.setTimeout(
                () => setPlaying(false),
                Math.ceil(remaining * 1000) + 50,
              );
              finish(true);
            }
            return;
          }

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
          source.onended = () => {
            sourcesRef.current = sourcesRef.current.filter((s) => s !== source);
          };
        };

        socket.onerror = () => finish(started);
        socket.onclose = () => {
          if (!started) setPlaying(false);
          finish(started);
        };
      }),
    [],
  );

  return { speak, stop: teardown };
}
