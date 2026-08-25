import { useCallback, useEffect, useRef, useState } from 'react';
import { getBase } from '../lib/api';
import { buildWsProtocols } from '../lib/useAgentEvents';

// openWakeWord's native frame size: 80ms @ 16kHz, 16-bit mono PCM.
const CHUNK_SAMPLES = 1280;
const TARGET_SAMPLE_RATE = 16000;

function buildWakeWordWsUrl(): string {
  const base = getBase();
  const url = new URL('/v1/speech/wake-word', base || window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
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
  const streamRef = useRef<MediaStream | null>(null);
  const pendingSamplesRef = useRef<number[]>([]);
  // Distinguishes "we closed the socket on purpose" (toggle off, unmount)
  // from an unexpected drop, so onclose knows whether to reconnect.
  const intentionalStopRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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

  const clearReconnectTimer = () => {
    if (reconnectTimeoutRef.current !== null) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  };

  const stop = useCallback(() => {
    sessionIdRef.current += 1;
    intentionalStopRef.current = true;
    clearReconnectTimer();
    processorRef.current?.disconnect();
    processorRef.current = null;
    sourceRef.current?.disconnect();
    sourceRef.current = null;
    silentGainRef.current?.disconnect();
    silentGainRef.current = null;
    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    pendingSamplesRef.current = [];
    setListening(false);
  }, []);

  // Creates (or re-creates) just the WebSocket leg of the pipeline. Kept
  // separate from `start` so an unexpected drop can reconnect without
  // re-requesting mic permission or tearing down the AudioContext.
  const connectSocket = useCallback(() => {
    const ws = new WebSocket(buildWakeWordWsUrl(), buildWsProtocols());
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectAttemptsRef.current = 0;
      setError(null);
      setListening(true);
    };
    ws.onmessage = (event) => {
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
      setListening(false);
      if (intentionalStopRef.current) return;

      // 1008/1011 are the server intentionally refusing pre-accept (bad
      // auth, no detector configured) — retrying would just repeat the
      // same refusal, so surface it immediately instead of looping.
      if (event.code === 1008 || event.code === 1011) {
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
        if (!intentionalStopRef.current) connectSocket();
      }, delay);
    };
  }, []);

  const start = useCallback(async () => {
    const mySession = ++sessionIdRef.current;
    setError(null);
    intentionalStopRef.current = false;
    reconnectAttemptsRef.current = 0;

    if (!navigator.mediaDevices?.getUserMedia) {
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

      connectSocket();

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
          ws.send(new Int16Array(chunk).buffer);
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

  return { listening, error };
}
