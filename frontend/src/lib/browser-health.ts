/**
 * Health checks only the browser can answer.
 *
 * The wake word once fired while the microphone was muted. Capture lives in
 * the page, not the server, so no server-side check can see a denied
 * permission or a missing device — this is the only place that failure is
 * visible. Kept as pure functions over a passed-in `navigator` so the logic
 * is testable: vitest here has no jsdom, so nothing that touches a real
 * browser global can be covered.
 */

export interface BrowserCheck {
  name: string;
  status: 'ok' | 'warn' | 'fail';
  message: string;
  details?: string;
}

interface DeviceLike {
  kind: string;
  label?: string;
  deviceId?: string;
}

/** Turn an enumerateDevices result and a permission state into checks. */
export function evaluateMicrophone(
  devices: DeviceLike[],
  permission: string | null,
): BrowserCheck[] {
  const mics = devices.filter((d) => d.kind === 'audioinput');
  const checks: BrowserCheck[] = [];

  if (permission === 'denied') {
    checks.push({
      name: 'Microphone permission',
      status: 'fail',
      message: 'Denied for this site',
      details:
        'The wake word and voice input cannot hear anything until this is ' +
        'granted in the browser’s site settings.',
    });
  } else if (permission === 'granted') {
    checks.push({
      name: 'Microphone permission',
      status: 'ok',
      message: 'Granted',
    });
  } else {
    checks.push({
      name: 'Microphone permission',
      status: 'warn',
      message: permission === 'prompt' ? 'Not yet granted' : 'Unknown',
      details:
        'The browser will ask the first time voice is used. Until then the ' +
        'wake word cannot listen.',
    });
  }

  if (mics.length === 0) {
    checks.push({
      name: 'Microphone devices',
      status: 'fail',
      message: 'No microphone found',
      details: 'Voice input has no device to capture from.',
    });
    return checks;
  }

  // Labels are empty until permission is granted, which is itself a signal
  // rather than a fault — it is how the browser hides device names.
  const named = mics.filter((d) => (d.label || '').trim().length > 0);
  checks.push({
    name: 'Microphone devices',
    status: 'ok',
    message:
      named.length > 0
        ? `${mics.length} found (${named[0].label})`
        : `${mics.length} found`,
    details:
      named.length === 0
        ? 'Device names are hidden until microphone permission is granted.'
        : undefined,
  });

  return checks;
}

/** Read the browser's own state. Returns an explanatory check on failure. */
export async function runBrowserChecks(): Promise<BrowserCheck[]> {
  const nav = typeof navigator === 'undefined' ? null : navigator;
  if (!nav?.mediaDevices?.enumerateDevices) {
    return [
      {
        name: 'Microphone',
        status: 'warn',
        message: 'This browser exposes no device information',
      },
    ];
  }

  let permission: string | null = null;
  try {
    // Firefox has no microphone permission descriptor; a throw here means
    // "unknown", not "denied".
    const status = await nav.permissions?.query({
      name: 'microphone' as PermissionName,
    });
    permission = status?.state ?? null;
  } catch {
    permission = null;
  }

  try {
    const devices = await nav.mediaDevices.enumerateDevices();
    return evaluateMicrophone(devices, permission);
  } catch (err) {
    return [
      {
        name: 'Microphone',
        status: 'warn',
        message: `Could not read devices (${
          err instanceof Error ? err.message : 'unknown'
        })`,
      },
    ];
  }
}
