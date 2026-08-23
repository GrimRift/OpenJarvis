import { useState, useCallback, useRef, useEffect } from 'react';
import { transcribeAudio, fetchSpeechHealth } from '../lib/api';

export type SpeechState = 'idle' | 'recording' | 'transcribing';

// Raw peak-amplitude threshold (0-1) above which a frame counts as "someone
// is talking." Calibrated the same way as the wake-word detector's own
// levels: ambient room noise on this raw (no AGC) capture sits well under
// 0.05, real speech comfortably clears it.
const VAD_SPEECH_THRESHOLD = 0.045;
// How long the level must stay under threshold, after speech was heard,
// before treating the utterance as finished. Short enough to feel snappy,
// long enough to survive a natural mid-sentence breath.
const VAD_SILENCE_MS = 850;
// Auto-stop never arms until at least this much speech-like audio has been
// seen — otherwise the leading pause before the user starts talking (very
// common right after a wake-word trigger) would itself read as "silence
// after speech" and cut the recording before they said anything.
const VAD_MIN_SPEECH_MS = 250;

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
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
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
          if (peak > VAD_SPEECH_THRESHOLD) {
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
