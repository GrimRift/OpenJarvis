import {
  rmsFromTimeDomain,
  resetSpeechLevel,
  setSpeechLevel,
  smoothLevel,
} from './audio-level';

/**
 * Drive the speech level from a Web Audio node, for as long as it plays.
 *
 * Returns a stop function. Every caller must invoke it, or the level stays
 * pinned at whatever was last measured and the orb keeps pulsing in silence.
 */
export function analyseInto(
  ctx: AudioContext,
  source: AudioNode,
  destination: AudioNode = ctx.destination,
): () => void {
  const analyser = ctx.createAnalyser();
  // Small window: this measures loudness, not pitch, and a large FFT would
  // smear syllable edges the orb is meant to show.
  analyser.fftSize = 512;
  const data = new Uint8Array(analyser.fftSize);

  source.connect(analyser);
  analyser.connect(destination);

  let raf = 0;
  let previous = 0;
  let current = 0;
  let stopped = false;

  const tick = (now: number) => {
    if (stopped) return;
    const dt = previous ? Math.min((now - previous) / (1000 / 60), 3) : 1;
    previous = now;

    analyser.getByteTimeDomainData(data);
    current = smoothLevel(current, rmsFromTimeDomain(data), dt);
    setSpeechLevel(current);

    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);

  return () => {
    if (stopped) return;
    stopped = true;
    cancelAnimationFrame(raf);
    resetSpeechLevel();
    try {
      source.disconnect(analyser);
      analyser.disconnect();
    } catch {
      // Graph already torn down.
    }
  };
}
