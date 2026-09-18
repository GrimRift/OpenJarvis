import { voiceTrace } from '../lib/voice-trace';
import { useCallback, useEffect, useRef, useState } from 'react';
import { getBase } from '../lib/api';
import type { FluxWord } from '../lib/barge-in';
import { buildWsProtocols } from '../lib/useAgentEvents';
import {
  adaptFloor,
  applyGain,
  nextGain,
  speechThreshold,
  totalGain,
  trackNoise,
} from '../lib/mic-gain';

// Same capture format the wake-word socket already uses, so the browser
// needs no second audio path: 16-bit mono PCM at 16kHz.
const TARGET_SAMPLE_RATE = 16000;
// int16 RMS above which a frame is taken to carry speech rather than the
// room. The floor: a quiet room measures ~350. The caller raises it for a
// loud room (`setSpeechLevel`): a desk fan measured ~1,000 median with
// peaks past 6,000, and against a fixed 350 it never looked quiet, so the
// pause greeting never came from the microphone.
const SPEECH_RMS_FLOOR = 350;
const SPEECH_RMS_CEILING = 6000;

function rms(pcm: Int16Array): number {
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
  return pcm.length ? Math.sqrt(sum / pcm.length) : 0;
}
// 50ms per frame. Small enough that end-of-turn isn't gated on a slow chunk,
// large enough to avoid a send() per animation frame.
const CHUNK_SAMPLES = 800;

// Keep at most this much of the current turn for local fallback. A turn that
// runs longer than this is past the point where re-transcribing locally is
// a better experience than reporting the failure.
const MAX_FALLBACK_SECONDS = 30;

export type FluxStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'unavailable'
  | 'error';

/**
 * Whether a turn opened on the socket can still be in flight.
 *
 * Only a connected socket can deliver the EndOfTurn that ends a turn. Every
 * other status means the socket that started it is gone -- including an
 * intentional teardown, which never reports itself through `onUnavailable`.
 * A turn left open outlives its socket and, because the caller reports
 * 'recording' while one is open and the wake word only fires from 'idle',
 * silently disables the wake word for the rest of the session.
 */
export function turnSurvivesStatus(status: FluxStatus): boolean {
  return status === 'connected';
}

export interface FluxTurn {
  event: 'StartOfTurn' | 'Update' | 'EagerEndOfTurn' | 'TurnResumed' | 'EndOfTurn';
  turnIndex: number;
  transcript: string;
  confidence: number;
}

export interface UseFluxSpeechOptions {
  /** Flux mode is selected and should hold a session open. */
  enabled: boolean;
  /** Ultra mode — ask the server for speculative EagerEndOfTurn events. */
  eager: boolean;
  /**
   * A new Deepgram session is connected. Turn indices restart at 0, so any
   * per-turn bookkeeping the caller keeps must restart too.
   */
  onSessionReady?: () => void;
  /**
   * The model this chat is actually using, so the speculative draft is made
   * by the same one that will answer.
   *
   * The server used to draft with its own startup model, which is always the
   * local one: every voice turn loaded a 3.6 GB model onto the GPU while the
   * real reply came from the cloud, and Ollama held it for five minutes
   * after. Sending the selection means "Prefer cloud model" governs drafting
   * too, and turning it off keeps drafting on-device as before.
   */
  model?: string;
  onEndOfTurn: (
    transcript: string,
    turnIndex: number,
    speculativeAnswer?: string,
  ) => void;
  onEagerEndOfTurn?: (transcript: string, turnIndex: number) => void;
  /**
   * Deepgram has heard speech and opened a turn.
   *
   * The caller needs this to tell "nobody said anything" from "someone is
   * still talking": Deepgram only ends turns it started, so a wake word that
   * fires on noise produces no events at all and nothing else would ever
   * release the microphone.
   */
  onTurnStarted?: (turnIndex: number) => void;
  /**
   * Partial transcript while a turn is in progress (never displayed), with
   * Deepgram's confidence in each word.
   */
  onUpdate?: (transcript: string, turnIndex: number, words: FluxWord[]) => void;
  onTurnResumed?: (turnIndex: number) => void;
  /**
   * Flux cannot be used, or failed mid-session. `audio` carries whatever of
   * the current turn was captured so the caller can transcribe it locally
   * instead of losing the utterance.
   */
  onUnavailable: (reason: string, audio: Int16Array | null) => void;
}

function fluxWords(raw: unknown): FluxWord[] {
  if (!Array.isArray(raw)) return [];
  const words: FluxWord[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const { word, confidence } = item as { word?: unknown; confidence?: unknown };
    if (typeof word !== 'string') continue;
    words.push({ word, confidence: typeof confidence === 'number' ? confidence : 0 });
  }
  return words;
}

/** What a server message means, decided without touching any state. */
export type FluxAction =
  | { kind: 'ignore' }
  | { kind: 'ready' }
  | { kind: 'unavailable'; reason: string }
  | { kind: 'turnStarted'; turnIndex: number }
  /**
   * A partial transcript of the turn in progress. Nothing in the UI shows
   * it; it exists so barge-in can count words while Sage is speaking.
   */
  | { kind: 'update'; turnIndex: number; transcript: string; words: FluxWord[] }
  | { kind: 'speculate'; turnIndex: number; transcript: string }
  | { kind: 'cancelSpeculation'; turnIndex: number }
  | {
      kind: 'endTurn';
      turnIndex: number;
      transcript: string;
      /**
       * A speculative answer the server released against this confirmed
       * turn. Present only on a final event — the server attaches it after
       * verifying turn identity and transcript, and discards anything
       * tool-shaped, so it never arrives unless it is safe to use.
       */
      speculativeAnswer?: string;
    };

/**
 * Interpret one server message.
 *
 * Pure so the state machine can be tested without a DOM, a socket, or an
 * AudioContext. `lastFinalTurn` makes a repeated EndOfTurn a no-op: Deepgram
 * may resend one, and sending the same turn twice would put two identical
 * questions into the conversation.
 */
// Backoff for transport-level drops. Bounded on purpose: if Flux cannot be
// reached after a few tries the page stays on local transcription, which is
// the fail-closed outcome, rather than reconnecting in a loop forever.
export const MAX_FLUX_RECONNECTS = 3;

export function reconnectDelay(attemptsSoFar: number): number | null {
  if (attemptsSoFar >= MAX_FLUX_RECONNECTS) return null;
  return 500 * 2 ** attemptsSoFar;
}

export function interpretFluxMessage(
  raw: string,
  lastFinalTurn: number | null,
): FluxAction {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(raw);
  } catch {
    return { kind: 'ignore' };
  }
  if (!data || typeof data !== 'object') return { kind: 'ignore' };

  const kind = data.type;
  if (kind === 'FluxReady') return { kind: 'ready' };
  if (kind === 'FluxUnavailable' || kind === 'FluxError') {
    return { kind: 'unavailable', reason: String(data.reason ?? 'Flux unavailable') };
  }
  if (kind !== 'TurnInfo') return { kind: 'ignore' };

  const turnIndex = Number(data.turn_index ?? 0);
  const transcript = String(data.transcript ?? '');

  switch (data.event) {
    case 'StartOfTurn':
      return { kind: 'turnStarted', turnIndex };
    case 'Update':
      return { kind: 'update', turnIndex, transcript, words: fluxWords(data.words) };
    case 'EagerEndOfTurn':
      return { kind: 'speculate', turnIndex, transcript };
    case 'TurnResumed':
      return { kind: 'cancelSpeculation', turnIndex };
    case 'EndOfTurn': {
      if (lastFinalTurn !== null && turnIndex <= lastFinalTurn) {
        // Repeated or out-of-order final for a turn already handled.
        return { kind: 'ignore' };
      }
      const released = data.speculative_answer;
      return {
        kind: 'endTurn',
        turnIndex,
        transcript,
        ...(typeof released === 'string' && released.trim()
          ? { speculativeAnswer: released }
          : {}),
      };
    }
    default:
      return { kind: 'ignore' };
  }
}

export function buildFluxWsUrl(eager: boolean, model?: string): string {
  const base = getBase();
  const url = new URL('/v1/speech/flux', base || window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  if (eager) url.searchParams.set('eager', '1');
  if (model) url.searchParams.set('model', model);
  return url.toString();
}

function downsample(input: Float32Array, fromRate: number): Int16Array {
  if (fromRate === TARGET_SAMPLE_RATE) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      out[i] = Math.max(-1, Math.min(1, input[i])) * 0x7fff;
    }
    return out;
  }
  const ratio = fromRate / TARGET_SAMPLE_RATE;
  const length = Math.floor(input.length / ratio);
  const out = new Int16Array(length);
  for (let i = 0; i < length; i++) {
    out[i] = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)])) * 0x7fff;
  }
  return out;
}

/**
 * Streaming transcription with model-based end-of-turn detection.
 *
 * Replaces the local silence timer only while Flux is the active mode; local
 * faster-whisper keeps its own endpointing. The socket is held open for the
 * voice session, but audio is only *sent* between `beginTurn()` and
 * `endTurn()` — idle microphone audio is never transmitted, including while
 * Sage is speaking.
 */
export function useFluxSpeech(options: UseFluxSpeechOptions) {
  const { enabled, eager, model } = options;
  // Read at connect time, never a dependency of the connect effect. Making it
  // one tore the socket down whenever the selected model changed -- and
  // `setCloudModelAvailable` changes it on load. A reconnect mid-turn means
  // the EndOfTurn that clears `fluxTurnActive` never arrives, the orb stays
  // stuck in 'recording', and the wake word can never re-arm because it only
  // fires from an idle state. A model chosen mid-session applies to the next
  // connection, which is the right trade against dropping a live turn.
  const modelRef = useRef(model);
  modelRef.current = model;
  const [status, setStatus] = useState<FluxStatus>('idle');
  const [reason, setReason] = useState<string>('');

  // Callbacks change every render; hold them in a ref so the socket handlers
  // never close over a stale one.
  const optsRef = useRef(options);
  optsRef.current = options;

  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const silentGainRef = useRef<GainNode | null>(null);
  /** Adaptive microphone gain, and the Settings slider on top of it. */
  const autoGainRef = useRef(1);
  /** The gain actually applied to the last frame, automatic times slider. */
  const gainRef = useRef(1);
  /** This stream's own measured room level. */
  const noiseRef = useRef(0);
  const manualGainRef = useRef(1);
  const streamRef = useRef<MediaStream | null>(null);
  const pendingRef = useRef<number[]>([]);

  // Gates transmission without tearing the socket down, so a turn can start
  // again without paying for a new handshake.
  const sendingRef = useRef(false);
  // The current turn's audio, kept only until the turn ends, for fallback.
  const fallbackRef = useRef<number[]>([]);
  // Distinguishes a deliberate close from a dropped connection.
  const intentionalStopRef = useRef(false);
  // Highest turn already finalised, so a repeated or out-of-order EndOfTurn
  // cannot send the same utterance twice.
  const lastFinalTurnRef = useRef<number | null>(null);
  // See useWakeWord: start() awaits getUserMedia, and `enabled` can flip
  // during that await. Every continuation re-checks this before touching a
  // ref, otherwise a stopped session resurrects an orphaned mic.
  const sessionIdRef = useRef(0);
  // A dropped socket used to strand the page on local transcription until it
  // was reloaded, because nothing reconnects outside the `enabled` effect.
  // Bounded, and only for transport faults: a FluxUnavailable verdict is the
  // server deciding Flux must not be used, and is never retried.
  const reconnectsRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const connectRef = useRef<(() => void) | null>(null);

  const takeFallbackAudio = useCallback((): Int16Array | null => {
    const samples = fallbackRef.current;
    if (!samples.length) return null;
    const out = new Int16Array(samples.length);
    out.set(samples);
    fallbackRef.current = [];
    return out;
  }, []);

  const teardown = useCallback(() => {
    intentionalStopRef.current = true;
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    sendingRef.current = false;
    sessionIdRef.current += 1;

    try {
      processorRef.current?.disconnect();
      sourceRef.current?.disconnect();
      silentGainRef.current?.disconnect();
    } catch {
      /* already torn down */
    }
    processorRef.current = null;
    sourceRef.current = null;
    silentGainRef.current = null;

    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    if (audioCtxRef.current && audioCtxRef.current.state !== 'closed') {
      audioCtxRef.current.close().catch(() => undefined);
    }
    audioCtxRef.current = null;

    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    }
    pendingRef.current = [];
    fallbackRef.current = [];
  }, []);

  const fail = useCallback(
    (why: string, next: FluxStatus) => {
      setStatus(next);
      setReason(why);
      const audio = takeFallbackAudio();
      teardown();
      optsRef.current.onUnavailable(why, audio);
    },
    [takeFallbackAudio, teardown],
  );

  const handleMessage = useCallback(
    (raw: string) => {
      const action = interpretFluxMessage(raw, lastFinalTurnRef.current);
      const cb = optsRef.current;

      switch (action.kind) {
        case 'ready':
          setStatus('connected');
          setReason('');
          // The socket reached the relay, so the next drop gets a fresh budget.
          reconnectsRef.current = 0;
          voiceTrace('flux.ready');
          cb.onSessionReady?.();
          break;
        case 'unavailable':
          fail(action.reason, 'unavailable');
          break;
        case 'turnStarted':
          voiceTrace('flux.startOfTurn', { turn: action.turnIndex });
          cb.onTurnStarted?.(action.turnIndex);
          break;
        case 'update':
          cb.onUpdate?.(action.transcript, action.turnIndex, action.words);
          break;
        case 'speculate':
          cb.onEagerEndOfTurn?.(action.transcript, action.turnIndex);
          break;
        case 'cancelSpeculation':
          cb.onTurnResumed?.(action.turnIndex);
          break;
        case 'endTurn':
          voiceTrace('flux.endOfTurn', {
            turn: action.turnIndex,
            chars: action.transcript.length,
            speculative: Boolean(action.speculativeAnswer),
          });
          lastFinalTurnRef.current = action.turnIndex;
          // Stop transmitting at once: anything after this is idle audio
          // or Sage's own reply.
          sendingRef.current = false;
          fallbackRef.current = [];
          cb.onEndOfTurn(
            action.transcript,
            action.turnIndex,
            action.speculativeAnswer,
          );
          break;
        default:
          break;
      }
    },
    [fail],
  );

  const connect = useCallback(async () => {
    if (wsRef.current) return;
    const session = ++sessionIdRef.current;
    intentionalStopRef.current = false;
    // Deepgram numbers turns per connection, from 0. The duplicate guard
    // keeps the last final index and ignores anything at or below it; kept
    // across a reconnect it swallowed every turn of the new session until
    // the count caught up -- three swallowed turns, then the user refreshed.
    lastFinalTurnRef.current = null;
    setStatus('connecting');

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          // On for this stream only. The laptop's fan sits beside its
          // built-in microphone and holds a steady 433 RMS, which no amount
          // of gain can separate from speech -- gain lifts both. The wake
          // word keeps raw audio: its threshold was tuned on unsuppressed
          // frames, and a detector is cheaper to re-tune than to re-train.
          noiseSuppression: true,
          autoGainControl: false,
        },
      });
    } catch (err) {
      setStatus('unavailable');
      const why = `microphone unavailable: ${String(err)}`;
      setReason(why);
      optsRef.current.onUnavailable(why, null);
      return;
    }
    if (session !== sessionIdRef.current) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    streamRef.current = stream;

    const ws = new WebSocket(buildFluxWsUrl(eager, modelRef.current), buildWsProtocols());
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') handleMessage(ev.data);
    };
    // fail() tears the session down and hands the caller its buffered audio,
    // so the turn in flight still gets transcribed locally. Reconnecting
    // afterwards is what lets the *next* wake word reach Flux again.
    const dropped = (why: string) => {
      const delay = reconnectDelay(reconnectsRef.current);
      voiceTrace('flux.dropped', {
        why,
        attempts: reconnectsRef.current,
        reconnectIn: delay === null ? 'never' : delay,
      });
      fail(why, 'error');
      if (delay === null) return;
      reconnectsRef.current += 1;
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        connectRef.current?.();
      }, delay);
    };

    ws.onerror = () => {
      if (session === sessionIdRef.current && !intentionalStopRef.current) {
        dropped('Flux connection error');
      }
    };
    ws.onclose = () => {
      if (session !== sessionIdRef.current || intentionalStopRef.current) return;
      dropped('Flux connection closed');
    };

    const ctx = new AudioContext();
    audioCtxRef.current = ctx;
    const source = ctx.createMediaStreamSource(stream);
    sourceRef.current = source;
    const processor = ctx.createScriptProcessor(4096, 1, 1);
    processorRef.current = processor;

    processor.onaudioprocess = (event) => {
      if (!sendingRef.current) return;
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      if (Date.now() < holdUntilRef.current) return;

      const raw = downsample(
        event.inputBuffer.getChannelData(0),
        ctx.sampleRate,
      );
      // Deepgram gets the boosted frame: the laptop's own microphone at
      // arm's length arrives far under the level a desk mic gives, and the
      // stream is opened with autoGainControl off on purpose.
      const level = rms(raw);
      // This stream measures its own room level rather than borrowing the
      // wake word's: with noise suppression on here and off there, a floor
      // imported from that stream describes a different signal entirely.
      noiseRef.current = trackNoise(noiseRef.current, level);
      autoGainRef.current = nextGain(autoGainRef.current, level, {
        silence: adaptFloor(noiseRef.current),
      });
      const gain = totalGain(autoGainRef.current, manualGainRef.current);
      gainRef.current = gain;
      const pcm = applyGain(raw, gain);

      // Silence is judged on the RAW frame against a raw threshold (see
      // setSpeechLevel). Judging the boosted frame meant the threshold had
      // to be scaled too, and that pinned it at the ceiling.
      if (level >= speechThreshold(noiseRef.current, gain, SPEECH_RMS_FLOOR, SPEECH_RMS_CEILING)) {
        lastSoundAtRef.current = Date.now();
      }
      for (let i = 0; i < pcm.length; i++) pendingRef.current.push(pcm[i]);

      // Retain the turn's audio so a mid-turn Flux failure can still be
      // transcribed locally rather than silently losing the utterance.
      const cap = TARGET_SAMPLE_RATE * MAX_FALLBACK_SECONDS;
      for (let i = 0; i < pcm.length; i++) fallbackRef.current.push(pcm[i]);
      if (fallbackRef.current.length > cap) {
        fallbackRef.current = fallbackRef.current.slice(-cap);
      }

      while (pendingRef.current.length >= CHUNK_SAMPLES) {
        const frame = Int16Array.from(
          pendingRef.current.splice(0, CHUNK_SAMPLES),
        );
        try {
          wsRef.current.send(frame.buffer);
        } catch {
          break;
        }
      }
    };

    // A muted gain node keeps the graph pulling audio without echoing the
    // microphone to the speakers.
    const silent = ctx.createGain();
    silent.gain.value = 0;
    silentGainRef.current = silent;
    source.connect(processor);
    processor.connect(silent);
    silent.connect(ctx.destination);
  }, [eager, fail, handleMessage]);

  const beginTurn = useCallback((preRoll?: Int16Array) => {
    pendingRef.current = [];
    fallbackRef.current = [];
    sendingRef.current = true;
    voiceTrace('flux.beginTurn', {
      socket: wsRef.current ? wsRef.current.readyState : 'none',
      preRollMs: preRoll ? Math.round((preRoll.length / TARGET_SAMPLE_RATE) * 1000) : 0,
    });
    // Audio from before this moment -- the wake phrase and the first words
    // after it -- goes first, so the turn starts where the user did.
    if (preRoll && preRoll.length && wsRef.current?.readyState === WebSocket.OPEN) {
      // The ring the pre-roll comes from holds raw frames (the wake word
      // boosts only what it sends to the detector), so this takes the full
      // gain -- otherwise the wake phrase arrives quieter than the words
      // after it and Deepgram spells it badly.
      const lifted = applyGain(
        preRoll,
        totalGain(autoGainRef.current, manualGainRef.current),
      );
      for (let i = 0; i < lifted.length; i++) fallbackRef.current.push(lifted[i]);
      for (let at = 0; at + CHUNK_SAMPLES <= lifted.length; at += CHUNK_SAMPLES) {
        try {
          wsRef.current.send(lifted.slice(at, at + CHUNK_SAMPLES).buffer);
        } catch {
          break;
        }
      }
    }
  }, []);

  // Frames are dropped while this is set: a greeting clip playing into an
  // open turn would come back as the user's words.
  const holdUntilRef = useRef(0);
  // When the microphone last carried sound at speech level. The pause
  // greeting after the wake word is decided on this, not on Deepgram's
  // first partial, which arrives later than the greeting could be needed.
  const lastSoundAtRef = useRef(0);
  const lastSoundAt = useCallback(() => lastSoundAtRef.current, []);
  const speechRmsRef = useRef(SPEECH_RMS_FLOOR);
  /** Set the speech level for the room: four times its ambient RMS, within
   * the floor and the ceiling. */
  /**
   * Seed this stream's room level from the wake word's measurement.
   *
   * Only a seed now: the frames themselves are the authority, because the
   * two streams no longer carry the same signal (noise suppression is on
   * here and off there). Before a turn has any frames it is the only
   * estimate available, and an opening word is better judged against a
   * stale number than against zero.
   */
  const setSpeechLevel = useCallback((ambientRms: number) => {
    if (noiseRef.current <= 0 && ambientRms > 0) noiseRef.current = ambientRms;
    speechRmsRef.current = speechThreshold(
      noiseRef.current,
      gainRef.current,
      SPEECH_RMS_FLOOR,
      SPEECH_RMS_CEILING,
    );
    return speechRmsRef.current;
  }, []);
  /** The Settings slider, multiplied into the automatic gain. */
  const setMicBoost = useCallback((boost: number) => {
    manualGainRef.current = Number.isFinite(boost) && boost > 0 ? boost : 1;
  }, []);
  const holdAudio = useCallback((promise: Promise<unknown>, tailMs = 250) => {
    holdUntilRef.current = Number.POSITIVE_INFINITY;
    const release = () => {
      holdUntilRef.current = Date.now() + tailMs;
    };
    promise.then(release, release);
  }, []);

  const endTurn = useCallback(() => {
    voiceTrace('flux.endTurn', {
      socket: wsRef.current ? wsRef.current.readyState : 'none',
    });
    sendingRef.current = false;
    pendingRef.current = [];
    // Tell the proxy to stop forwarding rather than closing the socket, so
    // the next turn does not pay for a new handshake.
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      try {
        ws.send('stop');
      } catch {
        /* the socket is going away anyway */
      }
    }
  }, []);

  const disconnect = useCallback(() => {
    teardown();
    setStatus('idle');
    setReason('');
  }, [teardown]);

  useEffect(() => {
    connectRef.current = () => {
      void connect();
    };
  }, [connect]);

  useEffect(() => {
    if (enabled) {
      void connect();
    } else {
      teardown();
      setStatus('idle');
    }
    return () => {
      teardown();
    };
    // connect/teardown are stable; re-running on `eager` is intended, since
    // the flag is part of the socket URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, eager]);

  return {
    status,
    reason,
    beginTurn,
    endTurn,
    holdAudio,
    lastSoundAt,
    setSpeechLevel,
    setMicBoost,
    connect,
    disconnect,
    takeFallbackAudio,
  };
}
