import { useEffect, useSyncExternalStore } from 'react';
import { isVoiceOwner, onVoiceOwnerChange, startVoiceOwnership } from '../lib/voice-owner';
import { voiceTrace } from '../lib/voice-trace';

/** Whether this page may listen (see lib/voice-owner.ts). */
export function useVoiceOwner(): boolean {
  return useSyncExternalStore(onVoiceOwnerChange, isVoiceOwner, isVoiceOwner);
}

/** Once, at the top of the app. */
export function useVoiceOwnership(): void {
  useEffect(() => {
    const stop = startVoiceOwnership();
    const unsubscribe = onVoiceOwnerChange((owner) => voiceTrace('voice.owner', { owner }));
    return () => {
      unsubscribe();
      stop();
    };
  }, []);
}
