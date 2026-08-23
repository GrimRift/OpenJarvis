import { useState, useCallback, useRef, useEffect } from 'react';
import { transcribeAudio, fetchSpeechHealth } from '../lib/api';

export type SpeechState = 'idle' | 'recording' | 'transcribing';

// A fixed absolute level cannot work across microphones: it was tuned
// against one mic and, on a more sensitive one, ambient room noise alone
// can sit near or above it, so the level never sustains long enough below
// threshold to ever count as silence — recording then runs out the clock on
// the hard fallback timer instead, which is indistinguishable from "the
// mic is stuck." The first CALIBRATION_MS of every recording are used to
// sample this specific mic's actual noise floor instead, and the speech
// threshold is set relative to that.
const VAD_CALIBRATION_MS = 300;
const VAD_NOISE_MULTIPLIER = 3;
// Still enforced as a lower bound — without it, a dead-silent room (noise
// floor near 0) would set a near-zero threshold that fires on the faintest
// breath.
const VAD_MIN_THRESHOLD = 0.02;
// And an upper bound: if the user starts talking with no pause after "Hey
// Sage" at all, the calibration window could mistake early speech for
// ambient noise and calibrate a threshold too high to ever see the rest of
// it as speech either. Capping it means the worst case degrades to the old
// fixed-threshold behaviour rather than to a threshold speech can't clear.
const VAD_MAX_THRESHOLD = 0.15;
// How long the level must stay under threshold, after speech was heard,
// before treating the utterance as finished. Short enough to feel snappy,
// long enough to survive a natural mid-sentence breath.
const VAD_SILENCE_MS = 850;
// Auto-stop never arms until at least this much speech-like audio has been
// seen — otherwise the leading pause before the user starts talking (very
// common right after a wake-word trigger) would itself read as "silence
// after speech" and cut the recording before they said anything.
const VAD_MIN_SPEECH_MS = 250;

/** Pure so the clamping can be unit-tested without mocking AudioContext. */
export function computeSpeechThreshold(noiseFloor: number): number {
  return Math.min(
    VAD_MAX_THRESHOLD,
    Math.max(VAD_MIN_THRESHOLD, noiseFloor * VAD_NOISE_MULTIPLIER),
  );
}

export function useSpeech() {
  const [state, setState] = useState<SpeechState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  const vadCtxRef = useRef<AudioContext | null>(null);
  const vadProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const vadSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const vadSilentGainRef = useRef<GainNode | null>(null);

  const teardownVad = useCallback(() => {
    vadProcessorRef.current?.disconnect();
    vadProcessorRef.current = null;
    vadSourceRef.current?.disconnect();
    vadSourceRef.current = null;
    vadSilentGainRef.current?.disconnect();
    vadSilentGainRef.current = null;
    vadCtxRef.current?.close().catch(() => {});
    vadCtxRef.current = null;
  }, []);

  // Check if speech backend is available on mount
  useEffect(() => {
    fetchSpeechHealth()
      .then((health) => setAvailable(health.available))
      .catch(() => setAvailable(false));
  }, []);

  const startRecording = useCallback(async (onSilence?: () => void): Promise<void> => {
    setError(null);

    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone not supported in this browser');
      return;
    }

    try {
      // Explicit rather than relying on browser defaults — Opera GX and
      // other Chromium variants don't necessarily apply the same defaults
      // as Chrome, and a quiet built-in laptop mic needs autoGainControl
      // to actually be on, not just assumed.
      //
      // The hands-free path (onSilence given) turns it off instead, same
      // fix already proven in useWakeWord.ts for the same reason: AGC
      // "boosts quiet transients... up toward speech-level loudness" —
      // on a mic whose true ambient noise is already near zero, AGC has
      // nothing real to normalise against, so it hunts and pumps the
      // level on its own. VAD reads that pumping as the user still
      // talking, so silence never sustains long enough to end the
      // recording — a real live case took 8+ seconds after "who are
      // you", none of it actual speech. A manual click has no such
      // stall to cause (the user stops it themselves), so it keeps the
      // original behaviour.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: !onSilence,
          channelCount: 1,
        },
      });
      streamRef.current = stream;

      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.start();
      mediaRecorderRef.current = recorder;

      // onSilence is only passed for the hands-free (wake-word /
      // continuous-conversation) path — a manual mic click always waits for
      // a second click, same as before. Without this, every hands-free turn
      // waited out a fixed multi-second timer regardless of how quickly the
      // user actually finished talking, which is what made the pause after
      // speaking feel like a stall rather than normal processing time.
      if (onSilence) {
        const audioCtx = new AudioContext();
        vadCtxRef.current = audioCtx;
        if (audioCtx.state === 'suspended') await audioCtx.resume();

        const source = audioCtx.createMediaStreamSource(stream);
        vadSourceRef.current = source;
        const processor = audioCtx.createScriptProcessor(2048, 1, 1);
        vadProcessorRef.current = processor;

        const startedAt = performance.now();
        let noiseSum = 0;
        let noiseSamples = 0;
        let speechThreshold: number | null = null;
        let hasSpokenAt: number | null = null;
        let lastLoudAt = 0;
        let fired = false;

        processor.onaudioprocess = (e) => {
          if (fired) return;
          const input = e.inputBuffer.getChannelData(0);
          let peak = 0;
          for (let i = 0; i < input.length; i++) {
            const abs = Math.abs(input[i]);
            if (abs > peak) peak = abs;
          }
          const now = performance.now();

          if (speechThreshold === null) {
            // Still sampling this mic's own noise floor — every frame in
            // this window is assumed to be ambient sound, not speech, on
            // the premise that "Hey Sage" has already been said and
            // processed by the wake-word listener before this recording
            // even starts, so there is normally a brief real gap here.
            noiseSum += peak;
            noiseSamples += 1;
            if (now - startedAt < VAD_CALIBRATION_MS) return;
            const noiseFloor = noiseSamples > 0 ? noiseSum / noiseSamples : 0;
            speechThreshold = computeSpeechThreshold(noiseFloor);
            lastLoudAt = now;
            return;
          }

          if (peak > speechThreshold) {
            lastLoudAt = now;
            if (hasSpokenAt === null) hasSpokenAt = now;
            return;
          }
          if (
            hasSpokenAt !== null &&
            now - hasSpokenAt > VAD_MIN_SPEECH_MS &&
            now - lastLoudAt > VAD_SILENCE_MS
          ) {
            fired = true;
            onSilence();
          }
        };

        // Chrome only fires onaudioprocess while routed through to a
        // destination — a zero-gain node keeps it live without playing the
        // mic back out the speakers.
        const silentGain = audioCtx.createGain();
        silentGain.gain.value = 0;
        vadSilentGainRef.current = silentGain;
        source.connect(processor);
        processor.connect(silentGain);
        silentGain.connect(audioCtx.destination);
      }
      setState('recording');
    } catch (err) {
      setError('Microphone access denied');
      setState('idle');
      teardownVad();
    }
  }, [teardownVad]);

  const stopRecording = useCallback(async (): Promise<string> => {
    return new Promise((resolve, reject) => {
      const recorder = mediaRecorderRef.current;
      if (!recorder || recorder.state !== 'recording') {
        reject(new Error('Not recording'));
        return;
      }

      recorder.onstop = async () => {
        setState('transcribing');
        teardownVad();

        // Stop all audio tracks
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;

        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        chunksRef.current = [];

        try {
          const result = await transcribeAudio(blob);
          setState('idle');
          resolve(result.text);
        } catch (err) {
          setState('idle');
          const msg = err instanceof Error ? err.message : 'Transcription failed';
          setError(msg);
          reject(err);
        }
      };

      recorder.stop();
    });
  }, [teardownVad]);

  return {
    state,
    error,
    available,
    startRecording,
    stopRecording,
    isRecording: state === 'recording',
    isTranscribing: state === 'transcribing',
  };
}
