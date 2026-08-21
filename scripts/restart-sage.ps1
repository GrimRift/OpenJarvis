# Restart Sage's backend (jarvis serve) and frontend (vite dev server) in
# fresh windows, killing whatever's already listening on their ports first.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\restart-sage.ps1

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendPort = 8000
$FrontendPorts = 5173, 5174  # vite falls back to 5174 if 5173 is busy

function Stop-PortProcess {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        $procId = $conn.OwningProcess
        if ($procId) {
            $procName = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
            Write-Host "Stopping $procName (PID $procId) on port $Port"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "Stopping existing Sage processes..."
Stop-PortProcess -Port $BackendPort
foreach ($p in $FrontendPorts) { Stop-PortProcess -Port $p }
Start-Sleep -Seconds 1

Write-Host "Starting backend (jarvis serve)..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$RepoRoot'; uv run jarvis serve"
)

Write-Host "Starting frontend (npm run dev)..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$RepoRoot\frontend'; npm run dev"
)

Write-Host "Sage is restarting in two new windows."
