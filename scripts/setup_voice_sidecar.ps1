<#
.SYNOPSIS
  Build the Python environment the Chatterbox voice sidecar runs in.

.DESCRIPTION
  Chatterbox pins torch==2.6.0, transformers==5.2.0 and numpy<2, none of
  which Sage's own venv can honour (torch 2.10, transformers 5.6, numpy 2),
  and an RTX 5050 (Blackwell) needs the CUDA 12.8 torch build in any case.
  So the sidecar gets its own uv-managed venv, outside the repo beside the
  rest of Sage's data, and `jarvis serve` runs it as a separate process.

  Re-runnable: an existing environment is updated in place.

.PARAMETER EnvDir
  Where to create the environment. Default: <OpenJarvis data dir>\voice-env
  (C:\AI\OpenJarvis-Data\voice-env unless OPENJARVIS_DATA is set).

.PARAMETER Cpu
  Install the CPU torch build instead of CUDA 12.8.
#>
param(
  [string]$EnvDir = "",
  [string]$ChatterboxRef = "5de7a54aa4e5e2baadb0182dde554908b48b85c2",
  [switch]$Cpu
)

$ErrorActionPreference = "Stop"

if (-not $EnvDir) {
  $data = if ($env:OPENJARVIS_DATA) { $env:OPENJARVIS_DATA } else { "C:\AI\OpenJarvis-Data" }
  $EnvDir = Join-Path $data "voice-env"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv is required (https://docs.astral.sh/uv/)."
}

Write-Host "Voice sidecar environment: $EnvDir"
if (-not (Test-Path (Join-Path $EnvDir "Scripts\python.exe"))) {
  uv venv $EnvDir --python 3.11
}
$py = Join-Path $EnvDir "Scripts\python.exe"

$torchIndex = if ($Cpu) { "https://download.pytorch.org/whl/cpu" } else { "https://download.pytorch.org/whl/cu128" }
Write-Host "Installing torch from $torchIndex"
uv pip install --python $py "torch>=2.7" "torchaudio>=2.7" --index-url $torchIndex

# From the repository, not PyPI: 0.1.7 on PyPI predates the Nano model
# (its from_pretrained has no `nano=` argument). Pinned to the commit that
# was verified here so a later upstream change cannot break a rebuild.
Write-Host "Installing Chatterbox without its pins"
uv pip install --python $py --no-deps "chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox.git@$ChatterboxRef"

# Chatterbox's own runtime dependencies, minus gradio (a demo UI) and with
# the pins it declares where they matter for model loading.
Write-Host "Installing Chatterbox's runtime dependencies"
uv pip install --python $py `
  "numpy>=1.24,<2" `
  "librosa==0.11.0" `
  "s3tokenizer" `
  "resemble-perth" `
  "conformer==0.3.2" `
  "diffusers==0.29.0" `
  "safetensors==0.5.3" `
  "transformers==5.2.0" `
  "pyloudnorm" `
  "omegaconf" `
  "huggingface_hub" `
  "fastapi" `
  "uvicorn[standard]" `
  "python-multipart" `
  "soundfile"

Write-Host "Checking the install"
& $py -c "import torch, chatterbox; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"

Write-Host ""
Write-Host "Done. The model weights (~1 GB) download from Hugging Face on the sidecar's first start."
