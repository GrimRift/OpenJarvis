/**
 * The browser's boosted output: a gain above unity followed by a limiter.
 *
 * `<audio>.volume` stops at 1, so a boosted `<audio>` element is routed
 * through Web Audio instead: element → gain → limiter → speakers. The
 * limiter (a hard-knee compressor) is what lets 100% be louder than the
 * file without a loud clip distorting. One context is shared; if it will
 * not run (no user gesture yet), the element plays on its own at the
 * plain volume, so nothing that used to be heard goes silent.
 */

import { type VolumeChannel, gainFor, volumeFor } from './volume';

let shared: AudioContext | null = null;
const attached = new WeakMap<HTMLMediaElement, GainNode>();

function outputContext(): AudioContext | null {
  if (typeof AudioContext === 'undefined') return null;
  if (!shared) {
    try {
      shared = new AudioContext();
    } catch {
      return null;
    }
  }
  return shared;
}

/** gain → limiter → destination; returns the gain to feed. */
export function boostedChain(ctx: AudioContext, channel: VolumeChannel): GainNode {
  const gain = ctx.createGain();
  gain.gain.value = gainFor(channel);
  gain.connect(limiter(ctx)).connect(ctx.destination);
  return gain;
}

/** A brick-wall-ish limiter: fast attack, high ratio, just under full scale. */
export function limiter(ctx: AudioContext): DynamicsCompressorNode {
  const node = ctx.createDynamicsCompressor();
  node.threshold.value = -3;
  node.knee.value = 0;
  node.ratio.value = 20;
  node.attack.value = 0.003;
  node.release.value = 0.1;
  return node;
}

/**
 * Route `el` through the boosted chain at this channel's level. Call again
 * before each play to pick up a changed slider. Falls back to the plain
 * element volume when Web Audio is unavailable or will not run.
 */
export function setBoostedVolume(el: HTMLMediaElement, channel: VolumeChannel): void {
  const ctx = outputContext();
  if (!ctx) {
    el.volume = volumeFor(channel);
    return;
  }
  let gain = attached.get(el);
  if (!gain) {
    try {
      const source = ctx.createMediaElementSource(el);
      gain = boostedChain(ctx, channel);
      source.connect(gain);
      attached.set(el, gain);
    } catch {
      el.volume = volumeFor(channel);
      return;
    }
  }
  gain.gain.value = gainFor(channel);
  el.volume = 1;
  if (ctx.state !== 'running') void ctx.resume().catch(() => {});
}
