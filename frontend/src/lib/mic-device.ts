/**
 * Which microphone Sage listens with.
 *
 * Without a choice the browser takes the Windows default input. The
 * Settings picker exists because the mic that sounds best is not always
 * the default (6 October: a dynamic USB mic reaches Sage raw, while NVIDIA
 * Broadcast's virtual mic carries the same voice already cleaned up).
 *
 * A chosen mic that is gone (unplugged, Broadcast closed) must never leave
 * Sage deaf: opening it falls back to the same device under a new id
 * (matched by its name), then to the default.
 */

export interface MicChoice {
  /** '' = the Windows default input. */
  id: string;
  label: string;
}

export interface MicOption {
  id: string;
  label: string;
}

/** The ids the browser uses for "whatever Windows picks", not a device. */
const PSEUDO_IDS = new Set(['default', 'communications']);

/** Real audio inputs, in the order the browser lists them. */
export function microphoneOptions(devices: readonly MediaDeviceInfo[]): MicOption[] {
  return devices
    .filter((d) => d.kind === 'audioinput' && d.deviceId && !PSEUDO_IDS.has(d.deviceId))
    .map((d, i) => ({ id: d.deviceId, label: d.label || `Microphone ${i + 1}` }));
}

/**
 * The device id to open for *choice* among *devices*: the same id if it is
 * still there, else a device with the same name, else '' (the default).
 */
export function resolveMic(choice: MicChoice, devices: readonly MediaDeviceInfo[]): string {
  if (!choice.id) return '';
  const options = microphoneOptions(devices);
  if (options.some((o) => o.id === choice.id)) return choice.id;
  const byName = choice.label ? options.find((o) => o.label === choice.label) : undefined;
  return byName ? byName.id : '';
}

/** Open the chosen microphone with *audio* constraints, falling back as above. */
export async function openMicrophone(
  audio: MediaTrackConstraints,
  choice: MicChoice,
): Promise<MediaStream> {
  const media = navigator.mediaDevices;
  if (!choice.id) return media.getUserMedia({ audio });
  let id = choice.id;
  try {
    id = resolveMic(choice, await media.enumerateDevices());
  } catch {
    // Listing failed: try the saved id as it is.
  }
  if (!id) return media.getUserMedia({ audio });
  try {
    return await media.getUserMedia({ audio: { ...audio, deviceId: { exact: id } } });
  } catch (err) {
    const name = (err as { name?: string } | null)?.name;
    if (name === 'OverconstrainedError' || name === 'NotFoundError' || name === 'NotReadableError') {
      return media.getUserMedia({ audio });
    }
    throw err;
  }
}
