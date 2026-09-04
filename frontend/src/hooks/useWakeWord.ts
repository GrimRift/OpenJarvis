import { useCallback, useEffect, useRef, useState } from 'react';
import { getBase } from '../lib/api';
import { buildWsProtocols } from '../lib/useAgentEvents';

// openWakeWord's native frame size: 80ms @ 16kHz, 16-bit mono PCM.
const CHUNK_SAMPLES = 1280;
const TARGET_SAMPLE_RATE = 16000;
export const WAKE_WORD_STALE_MS = 12_000;

/**
 * Whether the listener is dead and should be rebuilt.
 *
 * Staleness is judged purely on how long it has been since the server last
 * answered a frame. It used to also require the socket to be *open*, which
 * meant the one state the watchdog could never recover from was having no
 * socket at all — and that is a reachable dead end, not a transient one:
 * `onclose` schedules its retry only while the closing socket still belongs to
 * the current session, every flip of `enabled` bumps that session, and
 * `audioPlaying` flips twice around the digest's handover from streaming TTS
 * to the batch AudioPlayer. A close landing across one of those flips is
 * disowned, its reconnect is dropped, and nothing calls `connectSocket` again.
 * Reported as "after the morning digest the wake word never came back".
 *
 * `start()` refreshes `lastResponseAt`, so a listener that has only just been
 * built is never restarted out from under itself.
 */
export function shouldRestartWakeWord({
  now,
  lastResponseAt,
  fatal = false,
}: {
  now: number;
  lastResponseAt: number;
  /** Mic denied, or the server refusing outright. Retrying only loops. */
  fatal?: boolean;
}): boolean {
  if (fatal) return false;
  return now - lastResponseAt > WAKE_WORD_STALE_MS;
}

export function wakeWordSessionOwnsSocket(
  activeSessionId: number,
  socketSessionId: number,
  isCurrentSocket: boolean,
): boolean {
  return activeSessionId === socketSessionId && isCurrentSocket;
}

function buildWakeWordWsUrl(): string {
  const base = getBase();
  const url = new URL('/v1/speech/wake-word', base || window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

/**
 * The quietest frame worth scoring, as int16 RMS.
 *
 * Reported from real use: the wake word fired while every microphone was
 * muted at the hardware key, most often during video playback, and heard
 * nothing afterwards. Measured against the real model, digital silence peaks
 * at 0.15 and ±2-LSB dither at 0.50, both under the 0.79 threshold -- so a
 * genuinely silent frame cannot fire it and something was reaching the
 * classifier that was not the room. echoCancellation is the only stage
 * holding a signal the user is not making: it is fed the playback stream as
 * its reference, which is why firing tracked whatever was on screen.
 *
 * Rather than depend on that diagnosis, nothing below this level is scored at
 * all. Ordinary speech at arm's length measures in the hundreds to low
 * thousands; a muted capture measures ~0. The floor sits far enough below
 * speech to be inaudible as a change in sensitivity, and far enough above a
 * dead mic to end this class of false trigger outright.
 */
export const MIN_FRAME_RMS = 40;

export function carriesSound(frame: number[]): boolean {
  let sum = 0;
  for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
  return Math.sqrt(sum / frame.length) >= MIN_FRAME_RMS;
}

export function useWakeWord(onDetected: () => void, enabled: boolean) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onDetectedRef = useRef(onDetected);
  onDetectedRef.current = onDetected;

  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const silentGainRef = useRef<GainNode | null>(null);
  // Browsers set MediaStreamTrack.muted when the OS mutes the device, which
  // is the exact signal a hardware mic-mute key produces. Trusted over the
  // samples themselves: whatever reaches the buffer while the device is
  // muted is by definition not the user speaking.
  const trackMutedRef = useRef(false);
  const streamRef = useRef<MediaStream | null>(null);
  const pendingSamplesRef = useRef<number[]>([]);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The server answers every submitted 80 ms frame. If those replies stop,
  // the capture pipeline is stale even when the browser still calls the
  // socket OPEN (seen after a completed/silent Voice turn and a short idle).
  const lastResponseAtRef = useRef(Date.now());
  // Bumped on every start()/stop() transition. start() is async (awaits
  // mic permission, then AudioContext setup) — if `enabled` flips back to
  // false while that's in flight, stop() runs and nulls every ref, but
  // start()'s paused continuation doesn't know that: it resumes right
  // past the await and overwrites those refs with a brand new stream/
  // socket, orphaned from anything that could ever call stop() on it
  // again. start() captures this value at its own start and re-checks it
  // after every await; a mismatch means it's stale and must tear down
  // whatever it just acquired instead of wiring it up.
  const sessionIdRef = useRef(0);
  // A failure retrying cannot fix: mic permission denied, or the server
  // refusing the socket before accept. The watchdog rebuilds anything else,
  // so without this it would rebuild these on a 3s loop forever.
  const fatalRef = useRef(false);

  const clearReconnectTimer = () => {
    if (reconnectTimeoutRef.current !== null) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  };

  const stop = useCallback(() => {
    sessionIdRef.current += 1;
    clearReconnectTimer();
    processorRef.current?.disconnect();
    processorRef.current = null;
    sourceRef.current?.disconnect();
    sourceRef.current = null;
    trackMutedRef.current = false;
    silentGainRef.current?.disconnect();
    silentGainRef.current = null;
    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    const ws = wsRef.current;
    wsRef.current = null;
    ws?.close();
    pendingSamplesRef.current = [];
    setListening(false);
  }, []);

  // Creates (or re-creates) just the WebSocket leg of the pipeline. Kept
  // separate from `start` so an unexpected drop can reconnect without
  // re-requesting mic permission or tearing down the AudioContext.
  const connectSocket = useCallback((sessionId: number) => {
    if (sessionIdRef.current !== sessionId) return;
    const ws = new WebSocket(buildWakeWordWsUrl(), buildWsProtocols());
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    ws.onopen = () => {
      if (
        !wakeWordSessionOwnsSocket(
          sessionIdRef.current,
          sessionId,
          wsRef.current === ws,
        )
      ) {
        return;
      }
      reconnectAttemptsRef.current = 0;
      lastResponseAtRef.current = Date.now();
      setError(null);
      setListening(true);
    };
    ws.onmessage = (event) => {
      if (
        !wakeWordSessionOwnsSocket(
          sessionIdRef.current,
          sessionId,
          wsRef.current === ws,
        )
      ) {
        return;
      }
      lastResponseAtRef.current = Date.now();
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'detected') {
          onDetectedRef.current();
        }
      } catch {
        // ignore malformed payload
      }
    };
    ws.onerror = () => {};
    ws.onclose = (event) => {
      // stop() followed quickly by start() is the normal Voice re-arm path.
      // The old close event can arrive after the new session is live; it must
      // not mark that listener idle or schedule a competing reconnect.
      if (
        !wakeWordSessionOwnsSocket(
          sessionIdRef.current,
          sessionId,
          wsRef.current === ws,
        )
      ) {
        return;
      }
      wsRef.current = null;
      setListening(false);

      // 1008/1011 are the server intentionally refusing pre-accept (bad
      // auth, no detector configured) — retrying would just repeat the
      // same refusal, so surface it immediately instead of looping.
      if (event.code === 1008 || event.code === 1011) {
        fatalRef.current = true;
        setError(`Wake word connection dropped (code ${event.code})`);
        return;
      }

      // Anything else (1005, 1006, ...) is most likely a transient drop —
      // dev-server reload, brief network hiccup, backgrounded tab. Retry
      // with backoff instead of making the user re-toggle the feature, and
      // only bother them with a toast once retries have genuinely stalled.
      reconnectAttemptsRef.current += 1;
      if (reconnectAttemptsRef.current >= 3) {
        setError(`Wake word connection dropped (code ${event.code})`);
      }
      const delay = Math.min(1000 * 2 ** (reconnectAttemptsRef.current - 1), 15000);
      clearReconnectTimer();
      reconnectTimeoutRef.current = setTimeout(() => {
        if (sessionIdRef.current === sessionId) connectSocket(sessionId);
      }, delay);
    };
  }, []);

  const start = useCallback(async () => {
    const mySession = ++sessionIdRef.current;
    setError(null);
    reconnectAttemptsRef.current = 0;
    fatalRef.current = false;
    lastResponseAtRef.current = Date.now();

    if (!navigator.mediaDevices?.getUserMedia) {
      fatalRef.current = true;
      setError('Microphone not supported in this browser');
      return;
    }

    try {
      // echoCancellation stays on (needed so Sage's own TTS playback
      // doesn't get picked back up as input). noiseSuppression and
      // autoGainControl are deliberately OFF here, unlike a typical mic
      // capture: real-world testing showed the wake-word classifier's
      // scores for ambient noise, keyboard clicks, and actual speech all
      // converged into the same ~0.4-0.6 range with them on — AGC in
      // particular boosts quiet transients (keyboard clicks) up toward
      // speech-level loudness, which distorts exactly the signal the
      // classifier depends on to tell them apart. The classifier was
      // trained on relatively raw/unprocessed audio, so raw mic input
      // here keeps train/inference conditions closer to matching.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: false,
          autoGainControl: false,
          channelCount: 1,
        },
      });

      if (sessionIdRef.current !== mySession) {
        // stop() (or a newer start()) ran while getUserMedia was pending —
        // this session is stale. stop()'s refs were already nulled before
        // this continuation resumed, so nothing else will ever clean up
        // what we just acquired; do it here instead of wiring it up.
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;

      connectSocket(mySession);

      const audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
      // Browsers create AudioContext in a suspended state until explicitly
      // resumed — without this, onaudioprocess never fires and no audio is
      // ever captured, silently.
      if (audioCtx.state === 'suspended') {
        await audioCtx.resume();
      }

      if (sessionIdRef.current !== mySession) {
        // Same story, this time across the (rarer, but still async) resume().
        wsRef.current?.close();
        wsRef.current = null;
        audioCtx.close().catch(() => {});
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const nativeSampleRate = audioCtx.sampleRate;
      const resampleRatio = TARGET_SAMPLE_RATE / nativeSampleRate;

      const track = stream.getAudioTracks()[0];
      if (track) {
        trackMutedRef.current = track.muted;
        track.onmute = () => {
          trackMutedRef.current = true;
        };
        track.onunmute = () => {
          trackMutedRef.current = false;
        };
      }

      const source = audioCtx.createMediaStreamSource(stream);
      sourceRef.current = source;

      // 4096 native-rate samples per callback, a standard ScriptProcessor size.
      const processor = audioCtx.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (e) => {
        const input = e.inputBuffer.getChannelData(0);

        // Read the current socket on every callback rather than closing
        // over the one from setup — after a reconnect, wsRef points at a
        // new WebSocket, and audio must keep flowing to it.
        const ws = wsRef.current;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (trackMutedRef.current) return;
        const outLength = Math.round(input.length * resampleRatio);
        const pending = pendingSamplesRef.current;

        for (let i = 0; i < outLength; i++) {
          const srcIndex = i / resampleRatio;
          const i0 = Math.floor(srcIndex);
          const i1 = Math.min(i0 + 1, input.length - 1);
          const frac = srcIndex - i0;
          const sample = input[i0] * (1 - frac) + input[i1] * frac;
          const clamped = Math.max(-1, Math.min(1, sample));
          pending.push(Math.round(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff));
        }

        while (pending.length >= CHUNK_SAMPLES) {
          const chunk = pending.splice(0, CHUNK_SAMPLES);
          if (carriesSound(chunk)) ws.send(new Int16Array(chunk).buffer);
        }
      };

      // Chrome only fires onaudioprocess while the node is connected through
      // to a destination. Routing through a zero-gain node keeps it "live"
      // without playing the mic back out the speakers.
      const silentGain = audioCtx.createGain();
      silentGain.gain.value = 0;
      silentGainRef.current = silentGain;

      source.connect(processor);
      processor.connect(silentGain);
      silentGain.connect(audioCtx.destination);
    } catch {
      fatalRef.current = true;
      setError('Microphone access denied');
      stop();
    }
  }, [stop, connectSocket]);

  useEffect(() => {
    if (enabled) {
      start();
    } else {
      stop();
    }
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    const watchdog = window.setInterval(() => {
      const ctx = audioCtxRef.current;
      if (ctx?.state === 'suspended') {
        // Give a successful resume one full liveness window before deciding
        // that the entire capture graph must be rebuilt.
        lastResponseAtRef.current = Date.now();
        void ctx.resume().catch(() => undefined);
        return;
      }

      if (
        shouldRestartWakeWord({
          now: Date.now(),
          lastResponseAt: lastResponseAtRef.current,
          fatal: fatalRef.current,
        })
      ) {
        stop();
        void start();
      }
    }, 3000);
    return () => window.clearInterval(watchdog);
  }, [enabled, start, stop]);

  return { listening, error };
}
