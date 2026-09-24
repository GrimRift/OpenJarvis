import { IncrementalTtsOutbox } from '../lib/incremental-tts';
import { limiter, outputContext } from '../lib/audio-out';
import { gainFor } from '../lib/volume';
import { voiceTrace } from '../lib/voice-trace';
import {
  chunkDuration,
  decodePcmF32,
  interpretTtsMessage,
  FIRST_CHUNK_LEAD,
  nextStartTime,
  outputTailDelayMs,
  PlaybackGeneration,
  shouldShowSpeakingState,
} from '../lib/pcm-playback';
import { analyseInto } from '../lib/speech-analyser';
import { openTtsSocket } from '../lib/speech-transport';
import { useAppStore } from '../lib/store';
import type { VoiceProfile } from '../lib/voice-profiles';

let nextPlaybackOwner = 0;
const MAX_SOCKET_BUFFERED_BYTES = 256 * 1024;

export type IncrementalTtsOutcome =
  | 'spoken'
  | 'failed-before-audio'
  /** The browser would not start the page's audio: it needs a click first. */
  | 'blocked'
  | 'cancelled';

/**
 * How long a suspended AudioContext gets to resume when a reply's audio
 * arrives before the reply is given up as unplayable.
 */
const RESUME_WAIT_MS = 700;

export interface IncrementalTtsSession {
  push(delta: string): boolean;
  /** Speak the held text now: the model has stopped writing for a while. */
  flush?(): void;
  finish(): Promise<IncrementalTtsOutcome>;
  cancel(): void;
}

interface ActiveResult {
  settled: boolean;
  resolve: (outcome: IncrementalTtsOutcome) => void;
}

const ref = <T,>(current: T) => ({ current });

/**
 * One player for the whole app, not one per message box.
 *
 * It used to live in the hook's own refs, and Chat and Voice each render a
 * message box of their own: switching between them unmounted one, whose
 * cleanup stopped the sources and closed the AudioContext, and Sage went
 * quiet mid-sentence. The reply's text kept streaming into a player that
 * was gone. Built once at module level, the voice carries on across any
 * page change; only a deliberate stop -- the button, a barge-in, the user
 * carrying on their sentence -- ends it.
 */
function createStreamingTtsPlayer() {
  const playbackOwnerRef = ref(`streaming-tts-${++nextPlaybackOwner}`);
  const socketRef = ref<WebSocket | null>(null);
  const outboxRef = ref<IncrementalTtsOutbox | null>(null);
  const resultRef = ref<ActiveResult | null>(null);
  const ctxRef = ref<AudioContext | null>(null);
  const scheduledUntilRef = ref(0);
  /** Whether this playback has scheduled its first chunk yet. */
  const startedRef = ref(false);
  const sampleRateRef = ref(24000);
  const sourcesRef = ref<AudioBufferSourceNode[]>([]);
  const completionTimerRef = ref<number | null>(null);
  const generationsRef = ref(new PlaybackGeneration());
  // Where each chunk of the voice enters the graph, before the volume.
  const inputRef = ref<GainNode | null>(null);
  const stopAnalyserRef = ref<(() => void) | null>(null);

  const teardown = () => {
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
    inputRef.current = null;
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
  };

  const ensureContext = (): AudioContext => {
    const existing = ctxRef.current;
    if (existing && existing.state !== 'closed') return existing;
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    const created = new Ctor({ sampleRate: 24000 });
    ctxRef.current = created;
    return created;
  };

  /**
   * Resume the page's audio from inside a click or key press, the one
   * moment a browser allows it. After any reload -- a Sage restart, a
   * laptop restart, the dev server's own -- the context starts suspended,
   * and a user who only ever says "Hey Sage" never clicks: every reply was
   * then dropped unheard, the orb stayed on standing by, and follow-up
   * listening never re-armed (89 of 560 voice replies, 22-24 September).
   */
  const unlock = () => {
    try {
      const ctx = ensureContext();
      if (ctx.state !== 'running') void ctx.resume().catch(() => {});
    } catch {
      // No Web Audio at all; nothing to unlock.
    }
    const out = outputContext();
    if (out && out.state !== 'running') void out.resume().catch(() => {});
  };

  const begin = (voice: VoiceProfile): IncrementalTtsSession => {
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
      ctx = ensureContext();
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
    const input = ctx.createGain();
    const gain = ctx.createGain();
    // The user's chat-reply volume (Settings → Volume), master × chat,
    // with the boost on top; the limiter keeps a loud stretch clean.
    gain.gain.value = gainFor('chat');
    input.connect(gain);
    inputRef.current = input;
    const limit = limiter(ctx);
    gain.connect(limit);
    limit.connect(ctx.destination);
    // The orb hears the voice as it was spoken, before the volume: measured
    // after it, turning Sage down calmed the orb (median level 0.91 at
    // full volume, 0.67 at half), and the reminders the server speaks --
    // which it measures raw -- moved differently from chat replies.
    try {
      stopAnalyserRef.current = analyseInto(ctx, input, null);
    } catch {
      // No level for the orb this reply; playback is untouched.
    }

    let socket: WebSocket;
    try {
      socket = openTtsSocket();
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
          // Which engine synthesises this reply; the audio protocol back
          // is the same for both.
          provider: voice.provider,
        }),
      );
    };

    // Audio that arrived while a suspended context was being resumed.
    const waiting: ArrayBuffer[] = [];
    let resuming = false;

    socket.onmessage = (event) => {
      if (!generationsRef.current.isCurrent(generation)) return;
      if (typeof event.data === 'string') {
        const message = interpretTtsMessage(event.data);
        if (message.kind === 'ready') {
          outbox.markReady();
        } else if (message.kind === 'start') {
          const startPlayback = () => {
            sampleRateRef.current = message.sampleRate;
            scheduledUntilRef.current = 0;
            startedRef.current = false;
            started = true;
            if (shouldShowSpeakingState(message)) setPlaying(true);
            settleOutcome('spoken');
            for (const chunk of waiting.splice(0)) playChunk(chunk);
          };
          if (ctx.state === 'running') {
            startPlayback();
            return;
          }
          // Suspended: try to resume, holding the audio as it arrives,
          // rather than dropping the whole reply without a word.
          resuming = true;
          const resumed = ctx.resume().then(
            () => ctx.state === 'running',
            () => false,
          );
          const timeout = new Promise<boolean>((resolve) =>
            window.setTimeout(() => resolve(ctx.state === 'running'), RESUME_WAIT_MS),
          );
          void Promise.race([resumed, timeout]).then((running) => {
            resuming = false;
            if (!generationsRef.current.isCurrent(generation)) return;
            if (running) {
              startPlayback();
              return;
            }
            voiceTrace('tts.blocked', { state: ctx.state });
            waiting.length = 0;
            settleOutcome('blocked');
            failStream();
          });
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

      if (!generationsRef.current.isCurrent(generation)) return;
      if (resuming) {
        waiting.push(event.data as ArrayBuffer);
        return;
      }
      if (!started) return;
      playChunk(event.data as ArrayBuffer);
    };

    const playChunk = (data: ArrayBuffer) => {
      const samples = decodePcmF32(data);
      if (samples.length === 0) return;
      const rate = sampleRateRef.current;
      const buffer = ctx.createBuffer(1, samples.length, rate);
      buffer.copyToChannel(samples, 0);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(inputRef.current ?? ctx.destination);
      // The first chunk of a reply starts a third of a second out, so the
      // queue has something in hand before the speakers ask for it.
      if (startedRef.current && ctx.currentTime > scheduledUntilRef.current) {
        // The queue ran dry before this chunk came: a gap the listener
        // heard, wherever in the sentence it fell. Logged so the cause
        // (generation, the socket, the device) can be read off the trace.
        voiceTrace('tts.underrun', {
          gapMs: Math.round((ctx.currentTime - scheduledUntilRef.current) * 1000),
        });
      }
      const startAt = nextStartTime(
        ctx.currentTime,
        scheduledUntilRef.current,
        startedRef.current ? undefined : FIRST_CHUNK_LEAD,
      );
      startedRef.current = true;
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
      flush: () => outbox.flush(),
      finish: () => {
        outbox.finish();
        return outcome;
      },
      cancel: teardown,
    };
  };

  const speak = async (text: string, voice: VoiceProfile): Promise<boolean> => {
    if (!text.trim()) return false;
    const session = begin(voice);
    session.push(text);
    const result = await session.finish();
    // Cancellation is deliberate and must never trigger a batch replay.
    return result !== 'failed-before-audio' && result !== 'blocked';
  };

  return { begin, speak, stop: teardown, unlock };
}

let player: ReturnType<typeof createStreamingTtsPlayer> | null = null;

/** The shared player, built on first use. */
export function streamingTtsPlayer(): ReturnType<typeof createStreamingTtsPlayer> {
  if (!player) {
    const built = createStreamingTtsPlayer();
    player = built;
    if (typeof window !== 'undefined') {
      for (const type of ['pointerdown', 'keydown', 'touchstart'] as const) {
        window.addEventListener(type, built.unlock, { capture: true, passive: true });
      }
    }
  }
  return player;
}

/** Stream raw model deltas to Sage's server and queue returned PCM in order. */
export function useStreamingTts() {
  return streamingTtsPlayer();
}

// A hot swap of this module leaves two copies alive: the voice player then
// updates one store while the orb and the microphone read the other, and
// Sage speaks with the orb on "standing by" and the mic closed (24
// September, after an edit to the store). Reload the page instead.
if (import.meta.hot) import.meta.hot.accept(() => window.location.reload());
