# Local voice: Parakeet (STT) and Chatterbox Nano (TTS)

Sage's voice can run on the machine instead of the cloud. Speech-to-text
and the voice are chosen independently in **Settings → Speech**:

| | Cloud | Local |
|---|---|---|
| Speech-to-text | Deepgram Flux (streaming, Ultra speculation) | NVIDIA Parakeet Realtime EOU 120M |
| Voice | Cartesia | Chatterbox Nano, cloned from a reference recording |

"Use local voice models" flips both; the language model is untouched.
Whatever is chosen, the browser protocols are the same, so wake word,
fast-follow, barge-in, echo/loop guards, continuation, fillers and
cancellation behave identically.

## Parakeet

Runs inside the server process on onnxruntime (`speech/parakeet/`), from
the published ONNX export (`altunenes/parakeet-rs`, ~480 MB, downloaded to
`<data>/models/parakeet-realtime-eou` on first use). It answers on the same
socket as Flux (`/v1/speech/flux?provider=parakeet`) with the same
`StartOfTurn` / `Update` / `EndOfTurn` messages.

- **Confidence**: the greedy RNNT loop keeps the joint network's softmax
  for each emitted symbol; a word's confidence is the least sure of its
  sub-word pieces. Barge-in therefore works exactly as with Flux, though
  the values are peakier than Deepgram's.
- **Turn end**: the model's `<EOU>` fires only when the audio after
  speech is nearly silent, which an ordinary room's floor never is
  (measured: 0 of 6 phrases in a 40 s recording). A turn therefore ends
  when no new word has been heard for `parakeet_eot_timeout_ms` (900 ms).
  Tokens only appear on speech, so noise cannot hold a turn open.
- **No Ultra**: there is no eager end of turn, so speculation never
  starts; the Ultra toggle is disabled while Parakeet is selected.
- **Text**: lowercase, no punctuation, English only, no keyterm biasing.
- **Device**: `parakeet_device = "cuda"` (needs `onnxruntime-gpu` and the
  CUDA 13 runtime + cuDNN 9 pip packages; the DLL directories are put on
  PATH by `speech/_cuda_dlls.py`) or `"cpu"`. Measured per 160 ms chunk:
  22 ms on the RTX 5050, 40 ms on CPU. The CUDA build loads ~0.5–1 GB of
  VRAM for the life of the process.
- **Fallback**: any failure reports `FluxUnavailable`/`FluxError`, and the
  browser transcribes the buffered turn with faster-whisper as before.
- **Wake-word verifier**: `wake_word_verify_model = "parakeet"` reuses the
  loaded engine instead of tiny.en. Measured on the 339-clip corpus it is
  worse (7/220 positives vs tiny.en's 141/220, 384 ms vs 128 ms), because
  the clips end mid-phrase and Whisper prompted with the phrase completes
  it. The default stays tiny.en; `scripts/wake_word_verify_compare.py`
  reproduces the numbers.

## Chatterbox Nano

Runs as a separate process (`voice_sidecar/`) in its own Python
environment, because the package pins torch 2.6 / transformers 5.2 /
numpy<2 and the RTX 5050 needs the cu128 torch build. Build the
environment once:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_voice_sidecar.ps1
```

`jarvis serve` starts the sidecar at boot when Chatterbox is the chosen
voice (otherwise on first use), keeps it resident, and stops it with the
server. Logs: `<data>/logs/voice_sidecar.log`. Weights (~1 GB) download
from Hugging Face on the first start.

- **Voices** live under `<data>/voices/<name>/`: `reference.wav`
  (converted to mono 24 kHz), `conds.pt` (the cached conditioning) and
  `voice.json` (tuning). Upload from Settings; each recording is its own
  voice, selectable in the picker. Chatterbox uses only the **first
  10–15 s** of a reference, so for a long recording the window matters:
  cut the best 15 s and upload that.
- **Streaming**: Chatterbox has no waveform streaming. Each sentence the
  segmenter completes is generated whole and sent as it finishes; long
  sentences are generated in clause-sized pieces so audio arrives faster
  than it plays (a 6.6 s sentence used to leave 650 ms of silence
  mid-reply; now 0). First audio ≈ 500 ms after the first sentence ends.
- **Cancellation**: a cancel bumps a generation id on the sidecar; audio
  from a segment still on the GPU is discarded by id and never heard.
- **Character**: comes from the recording. Sampling stays near the library
  defaults (temperature 0.7, top_p 0.95, top_k 1000, repetition 1.2);
  pushing them toward "calm" (0.5 / 300 / 1.3) stopped the end-of-speech
  token being sampled and a two-sentence input ran to 31 s. A bench of 24
  clips per setting, transcribed back with Whisper, found temperature
  0.55 repeats a phrase in 4 of 24 and 0.7 in 0 of 24: lower temperature
  is where "Sage repeats words" came from. The runaway guard (audio over
  90 ms/char + 1 s; clean clips fit 75 ms/char + 0.4 s) resamples twice
  at the library defaults before it truncates. Output is levelled
  per segment (`target_rms` 0.16) so the volume does not drift.
  `cfg_weight`, `exaggeration` and `min_p` are accepted by the library
  and ignored by Turbo/Nano, so they are not offered.
- **VRAM**: ~1.4 GB for the process in fp32. The weights are 1.7 GB
  (the "110M" is the text model alone), but the speech tokenizer and
  speaker encoder -- 0.5 GB used only to turn a reference recording into
  conditioning, which is cached per voice -- live on the CPU and visit
  the GPU for that one call. `chatterbox_precision = "fp16"` saves
  another ~0.3 GB but the half-precision vocoder audibly degrades the
  voice (metallic; A/B'd 22 September), so it is only for cards that
  cannot hold fp32.
- **The other GPU tenant**: with Ollama installed, the speculative draft
  for a voice turn used to fall back to the server's local model when the
  browser sent none -- which it does on the first socket of every page,
  opened before the model list loads -- putting qwen3.5:4b (4.2 GB) on
  the card for five minutes after the first turn. The browser now reopens
  that socket with the model once known, and the server never drafts on
  a local model nobody selected.
- **Server-side speech** (moments, reminders, the digest) follows the
  Settings choice: the page mirrors it to `<data>/voice_choice.json`.
  Waze audio stays on Cartesia (the phone wants MP3).

## Benchmark

`scripts/voice_latency_bench.py` drives the real sockets (needs a running
server; uses `OPENJARVIS_API_KEY` from the environment). Measured 22
September 2026 on this machine, 3 rounds:

| | end of audio → final | first audio | 7.5 s reply done |
|---|---|---|---|
| Deepgram Flux | 240–1500 ms (varies with the network) | | |
| Parakeet (GPU) | ~410 ms (900 ms timeout after the last word) | | |
| Cartesia | | 365 ms | 1.7 s |
| Chatterbox Nano | | ~500 ms (sidecar) / ~900 ms (through the server, paced deltas) | 3–4 s uncontended |

Chatterbox slows sharply when the GPU is near full (8 GB shared with
Whisper, Parakeet, Ollama and the browser's video decode): a sentence
that takes 0.5 s took 5.7 s at 7.6 GB used. Keep an eye on the Health
page's GPU line.

## Config keys (`[speech]`)

```
parakeet_enabled = true        parakeet_device = "cuda"      parakeet_quant = ""
parakeet_eot_timeout_ms = 900  parakeet_model_dir = ""
tts_provider = "cartesia"      chatterbox_voice = "jarvis"   chatterbox_device = "cuda"
chatterbox_precision = "fp32"  chatterbox_sidecar_port = 8791  chatterbox_env_dir = ""
wake_word_verify_model = "tiny.en"
```
