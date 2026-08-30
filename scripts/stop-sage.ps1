$ErrorActionPreference = "Stop"

$repoRoot = "C:\AI\OpenJarvis-Lab"
$sagePorts = 8000, 5173
$stoppedAny = $false
$refusedAny = $false

foreach ($port in $sagePorts) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if (-not $process) {
            continue
        }

        $isSageBackend = $port -eq 8000 -and
            $process.Name -eq "python.exe" -and
            $process.CommandLine -like "*$repoRoot*" -and
            $process.CommandLine -match "jarvis.*serve"

        $isSageFrontend = $port -eq 5173 -and
            $process.Name -eq "node.exe" -and
            $process.CommandLine -like "*$repoRoot\frontend*" -and
            $process.CommandLine -match "vite"

        if ($isSageBackend -or $isSageFrontend) {
            Stop-Process -Id $listener.OwningProcess -Force
            $stoppedAny = $true
        }
        else {
            $refusedAny = $true
        }
    }
}

if ($refusedAny) {
    exit 5
}

if ($stoppedAny) {
    exit 0
}

exit 4
