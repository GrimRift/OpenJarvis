import { useCallback, useEffect, useRef, useState } from 'react';
import { getBase } from '../lib/api';
import { buildWsProtocols } from '../lib/useAgentEvents';

// Same capture format the wake-word socket already uses, so the browser
// needs no second audio path: 16-bit mono PCM at 16kHz.
const TARGET_SAMPLE_RATE = 16000;
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
  onTurnResumed?: (turnIndex: number) => void;
  /**
   * Flux cannot be used, or failed mid-session. `audio` carries whatever of
   * the current turn was captured so the caller can transcribe it locally
   * instead of losing the utterance.
   */
  onUnavailable: (reason: string, audio: Int16Array | null) => void;
}

/** What a server message means, decided without touching any state. */
export type FluxAction =
  | { kind: 'ignore' }
  | { kind: 'ready' }
  | { kind: 'unavailable'; reason: string }
  | { kind: 'turnStarted'; turnIndex: number }
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
      // Update carries no decision; partial transcripts stay internal.
      return { kind: 'ignore' };
  }
}

function buildFluxWsUrl(eager: boolean, model?: string): string {
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
          break;
        case 'unavailable':
          fail(action.reason, 'unavailable');
          break;
        case 'turnStarted':
          cb.onTurnStarted?.(action.turnIndex);
          break;
        case 'speculate':
          cb.onEagerEndOfTurn?.(action.transcript, action.turnIndex);
          break;
        case 'cancelSpeculation':
          cb.onTurnResumed?.(action.turnIndex);
          break;
        case 'endTurn':
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
    setStatus('connecting');

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: false,
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

    const ws = new WebSocket(buildFluxWsUrl(eager, model), buildWsProtocols());
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') handleMessage(ev.data);
    };
    // fail() tears the session down and hands the caller its buffered audio,
    // so the turn in flight still gets transcribed locally. Reconnecting
    // afterwards is what lets the *next* wake word reach Flux again.
    const dropped = (why: string) => {
      fail(why, 'error');
      const delay = reconnectDelay(reconnectsRef.current);
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

      const pcm = downsample(
        event.inputBuffer.getChannelData(0),
        ctx.sampleRate,
      );
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
  }, [eager, model, fail, handleMessage]);

  const beginTurn = useCallback(() => {
    pendingRef.current = [];
    fallbackRef.current = [];
    sendingRef.current = true;
  }, []);

  const endTurn = useCallback(() => {
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
  }, [enabled, eager, model]);

  return {
    status,
    reason,
    beginTurn,
    endTurn,
    connect,
    disconnect,
    takeFallbackAudio,
  };
}
