import { getBase } from './api';
import { buildWsProtocols } from './useAgentEvents';

export function openTtsSocket(): WebSocket {
  const url = new URL('/v1/speech/tts-stream', getBase() || window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return new WebSocket(url.toString(), buildWsProtocols());
}
