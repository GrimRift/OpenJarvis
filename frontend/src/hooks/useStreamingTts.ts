import { useCallback, useEffect, useRef } from 'react';
import { IncrementalTtsOutbox } from '../lib/incremental-tts';
import {
  chunkDuration,
  decodePcmF32,
  interpretTtsMessage,
  nextStartTime,
  outputTailDelayMs,
  PlaybackGeneration,
  shouldShowSpeakingState,
} from '../lib/pcm-playback';
import { analyseInto } from '../lib/speech-analyser';
import { useAppStore } from '../lib/store';
import type { VoiceProfile } from '../lib/voice-profiles';

let nextPlaybackOwner = 0;
const MAX_SOCKET_BUFFERED_BYTES = 256 * 1024;

export type IncrementalTtsOutcome =
  | 'spoken'
  | 'failed-before-audio'
  | 'cancelled';

export interface IncrementalTtsSession {
  push(delta: string): boolean;
  finish(): Promise<IncrementalTtsOutcome>;
  cancel(): void;
}

interface ActiveResult {
  settled: boolean;
  resolve: (outcome: IncrementalTtsOutcome) => void;
}

/** Stream raw model deltas to Sage's server and queue returned PCM in order. */
export function useStreamingTts() {
  const playbackOwnerRef = useRef('');
  if (!playbackOwnerRef.current) {
    playbackOwnerRef.current = `streaming-tts-${++nextPlaybackOwner}`;
  }
  const socketRef = useRef<WebSocket | null>(null);
  const outboxRef = useRef<IncrementalTtsOutbox | null>(null);
  const resultRef = useRef<ActiveResult | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const scheduledUntilRef = useRef(0);
  const sampleRateRef = useRef(24000);
  const sourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const completionTimerRef = useRef<number | null>(null);
  const generationsRef = useRef(new PlaybackGeneration());
  const gainRef = useRef<GainNode | null>(null);
  const stopAnalyserRef = useRef<(() => void) | null>(null);

  const teardown = useCallback(() => {
    outboxRef.current?.cancel();
    outboxRef.current = null;
    const result = resultRef.current;
    if (result && !result.settled) {
      result.settled = true;
      result.resolve('cancelled');
    }
    resultRef.current = null;
    generationsRef.current.cancel();
    if (completionTimerRef.current !== null) {
      window.clearTimeout(completionTimerRef.current);
      completionTimerRef.current = null;
    }
    for (const source of sourcesRef.current) {
      try {
        source.stop();
      } catch {
        // Already finished.
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

  const begin = useCallback(
    (voice: VoiceProfile): IncrementalTtsSession => {
      teardown();
      const generation = generationsRef.current.begin();
      const owner = playbackOwnerRef.current;

      let started = false;
      let streamComplete = false;
      const activeSources = new Set<AudioBufferSourceNode>();
      let resolveOutcome!: (outcome: IncrementalTtsOutcome) => void;
      const outcome = new Promise<IncrementalTtsOutcome>((resolve) => {
        resolveOutcome = resolve;
      });
      const result: ActiveResult = { settled: false, resolve: resolveOutcome };
      resultRef.current = result;
      const settleOutcome = (value: IncrementalTtsOutcome) => {
        if (result.settled) return;
        result.settled = true;
        result.resolve(value);
      };

      const setPlaying = (playing: boolean) => {
        if (!generationsRef.current.isCurrent(generation)) return;
        useAppStore.getState().setAudioPlayback(owner, playing);
      };

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
          ctx = new Ctor({ sampleRate: 24000 });
        }
      } catch {
        setPlaying(false);
        settleOutcome('failed-before-audio');
        return {
          push: () => false,
          finish: () => outcome,
          cancel: teardown,
        };
      }
      ctxRef.current = ctx;
      stopAnalyserRef.current?.();
      const gain = ctx.createGain();
      gainRef.current = gain;
      try {
        stopAnalyserRef.current = analyseInto(ctx, gain);
      } catch {
        gain.connect(ctx.destination);
      }

      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      let socket: WebSocket;
      try {
        socket = new WebSocket(
          `${proto}://${window.location.host}/v1/speech/tts-stream`,
        );
      } catch {
        setPlaying(false);
        settleOutcome('failed-before-audio');
        return {
          push: () => false,
          finish: () => outcome,
          cancel: teardown,
        };
      }
      socket.binaryType = 'arraybuffer';
      socketRef.current = socket;

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

      const failStream = () => {
        if (!generationsRef.current.isCurrent(generation)) return;
        try {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'cancel' }));
          }
          socket.close();
        } catch {
          // The failure path is already terminal.
        }
        if (started) {
          settleOutcome('spoken');
          streamComplete = true;
          settlePlaying();
        } else {
          settleOutcome('failed-before-audio');
          setPlaying(false);
        }
      };

      const outbox = new IncrementalTtsOutbox((message) => {
        if (!generationsRef.current.isCurrent(generation)) return;
        if (
          socket.readyState !== WebSocket.OPEN ||
          socket.bufferedAmount > MAX_SOCKET_BUFFERED_BYTES
        ) {
          failStream();
          return;
        }
        socket.send(JSON.stringify(message));
      });
      outboxRef.current = outbox;

      socket.onopen = () => {
        if (!generationsRef.current.isCurrent(generation)) {
          socket.close();
          return;
        }
        void ctx.resume().catch(() => {});
        socket.send(
          JSON.stringify({
            type: 'begin',
            voice_id: voice.id,
            speed: voice.speed,
            volume: voice.volume,
          }),
        );
      };

      socket.onmessage = (event) => {
        if (!generationsRef.current.isCurrent(generation)) return;
        if (typeof event.data === 'string') {
          const message = interpretTtsMessage(event.data);
          if (message.kind === 'ready') {
            outbox.markReady();
          } else if (message.kind === 'start') {
            if (ctx.state !== 'running') {
              failStream();
              return;
            }
            sampleRateRef.current = message.sampleRate;
            scheduledUntilRef.current = 0;
            started = true;
            if (shouldShowSpeakingState(message)) setPlaying(true);
            settleOutcome('spoken');
          } else if (message.kind === 'error') {
            if (!message.started && !started) {
              settleOutcome('failed-before-audio');
              setPlaying(false);
            } else {
              settleOutcome('spoken');
              streamComplete = true;
              settlePlaying();
            }
          } else if (message.kind === 'done') {
            streamComplete = true;
            settlePlaying();
            settleOutcome(started ? 'spoken' : 'failed-before-audio');
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
        scheduledUntilRef.current = startAt + chunkDuration(samples.length, rate);
        sourcesRef.current.push(source);
        activeSources.add(source);
        source.onended = () => {
          sourcesRef.current = sourcesRef.current.filter((item) => item !== source);
          activeSources.delete(source);
          settlePlaying();
        };
      };

      socket.onerror = failStream;
      socket.onclose = () => {
        if (!generationsRef.current.isCurrent(generation)) return;
        if (started) {
          settleOutcome('spoken');
          streamComplete = true;
          settlePlaying();
        } else {
          settleOutcome('failed-before-audio');
          setPlaying(false);
        }
      };

      return {
        push: (delta: string) => {
          const accepted = outbox.push(delta);
          if (!accepted && outbox.overflowed) failStream();
          return accepted;
        },
        finish: () => {
          outbox.finish();
          return outcome;
        },
        cancel: teardown,
      };
    },
    [teardown],
  );

  const speak = useCallback(
    async (text: string, voice: VoiceProfile): Promise<boolean> => {
      if (!text.trim()) return false;
      const session = begin(voice);
      session.push(text);
      const result = await session.finish();
      // Cancellation is deliberate and must never trigger a batch replay.
      return result !== 'failed-before-audio';
    },
    [begin],
  );

  return { begin, speak, stop: teardown };
}
