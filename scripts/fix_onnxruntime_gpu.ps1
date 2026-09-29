<#
.SYNOPSIS
  Leave only the GPU onnxruntime in a venv synced with speech-parakeet-gpu.

.DESCRIPTION
  onnxruntime (CPU) and onnxruntime-gpu both install the `onnxruntime`
  module, so whichever lands last overwrites the other. faster-whisper
  (speech) and openwakeword (wake-word) require the CPU package, so uv.lock
  always carries it, and uv cannot drop it only when speech-parakeet-gpu is
  chosen. Run this after every `uv sync` that includes speech-parakeet-gpu:
  it removes the CPU package and reinstalls the same onnxruntime-gpu version
  so its files are intact. faster-whisper and openwakeword run fine on the
  GPU build.

  Does nothing when the CPU package is absent or the GPU one isn't installed.

.PARAMETER Python
  The venv's python.exe. Default: <repo>\.venv\Scripts\python.exe
#>
param(
  [string]$Python = ""
)

# No $ErrorActionPreference = "Stop": Windows PowerShell 5.1 turns uv's
# informational stderr into a terminating error. Exit codes are checked instead.
if (-not $Python) {
  $Python = Join-Path (Split-Path -Parent $PSScriptRoot) ".venv\Scripts\python.exe"
}

function Get-InstalledVersion([string]$Name) {
  $line = uv pip show --python $Python $Name 2>$null | Select-String '^Version: '
  if ($line) { return ($line -replace '^Version: ', '').Trim() }
  return $null
}

$gpu = Get-InstalledVersion "onnxruntime-gpu"
$cpu = Get-InstalledVersion "onnxruntime"
if (-not $gpu) { Write-Host "onnxruntime-gpu not installed; nothing to do."; exit 0 }
if (-not $cpu) { Write-Host "Only onnxruntime-gpu $gpu is installed; nothing to do."; exit 0 }

Write-Host "Removing CPU onnxruntime $cpu, reinstalling onnxruntime-gpu $gpu..."
uv pip uninstall --python $Python onnxruntime
if ($LASTEXITCODE -ne 0) { throw "uv pip uninstall onnxruntime failed" }
uv pip install --python $Python --reinstall --no-deps "onnxruntime-gpu==$gpu"
if ($LASTEXITCODE -ne 0) { throw "uv pip install onnxruntime-gpu failed" }
Write-Host "Done: onnxruntime-gpu $gpu only."
