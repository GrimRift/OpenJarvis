"""Chatterbox Nano as a local voice for Sage, in its own process.

This package is deliberately free of ``openjarvis`` imports: it runs inside
the sidecar's own Python environment (see ``scripts/setup_voice_sidecar.ps1``
for why that environment exists) and talks to the server over localhost.

  GET  /health                 model, device, current voice
  GET  /voices                 every voice under the voices directory
  POST /voices/{name}          upload a reference recording; conditioning is
                               computed once and cached beside it
  PUT  /voices/{name}/params   generation tuning for that voice
  DELETE /voices/{name}
  POST /synthesize             one utterance -> WAV (greetings, tests, batch)
  WS   /stream                 segment-by-segment synthesis for live replies

The streaming socket carries one reply at a time. Segments arrive as the
language model writes them; each is synthesised whole (Chatterbox has no
waveform streaming) and its audio sent as it finishes, so the first
sentence plays while the rest is still being written. A ``cancel`` drops
every queued segment, and audio from a segment already on the GPU is
discarded by generation id, so nothing from an interrupted reply can be
heard later.
"""

__version__ = "0.1.0"
