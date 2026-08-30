$ErrorActionPreference = "Stop"

$repoRoot = "C:\AI\OpenJarvis-Lab"
$frontendRoot = Join-Path $repoRoot "frontend"
$jarvisExe = Join-Path $repoRoot ".venv\Scripts\jarvis.exe"
$logDir = "C:\AI\OpenJarvis-Data\logs"
$stdoutLog = Join-Path $logDir "sage-server.log"
$stderrLog = Join-Path $logDir "sage-server-error.log"
$frontendStdoutLog = Join-Path $logDir "sage-frontend.log"
$frontendStderrLog = Join-Path $logDir "sage-frontend-error.log"
$healthUrl = "http://127.0.0.1:8000/health"
$frontendHealthUrl = "http://127.0.0.1:5173"
$frontendUrl = "http://localhost:5173"

$createdNew = $false
$launcherMutex = New-Object System.Threading.Mutex($true, "Local\SageStartMenuLauncher", [ref]$createdNew)
if (-not $createdNew) {
    $launcherMutex.Dispose()
    exit 0
}

function Test-SageHealth {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $response.status -eq "ok"
    }
    catch {
        return $false
    }
}

function Test-SageFrontend {
    try {
        $response = Invoke-WebRequest -Uri $frontendHealthUrl -TimeoutSec 2 -UseBasicParsing
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $jarvisExe -PathType Leaf)) {
    exit 2
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$backendWasReady = Test-SageHealth
$frontendWasReady = Test-SageFrontend

if ($backendWasReady -and $frontendWasReady) {
    exit 0
}

if (-not $backendWasReady) {
    Start-Process -FilePath $jarvisExe `
        -ArgumentList "serve" `
        -WorkingDirectory $repoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog
}

if (-not $frontendWasReady) {
    $npmExe = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $npmExe) {
        exit 3
    }

    Start-Process -FilePath $npmExe `
        -ArgumentList "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort" `
        -WorkingDirectory $frontendRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $frontendStdoutLog `
        -RedirectStandardError $frontendStderrLog
}

$deadline = (Get-Date).AddSeconds(120)
do {
    Start-Sleep -Seconds 2
    if ((Test-SageHealth) -and (Test-SageFrontend)) {
        Start-Process $frontendUrl
        exit 0
    }
} while ((Get-Date) -lt $deadline)

exit 1
