param(
    [string]$Python = "",
    [string]$HostAddr = "127.0.0.1",
    [int]$Port = 8088
)

$Root = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { $Python = $venvPy } else { $Python = "python" }
}

$env:PORT = "$Port"
$env:TIA_ALGOLIB_GATEWAY_PORT = "$Port"
$env:TIA_ALGOLIB_PREDICT_HOST = $HostAddr
$env:ALGOLIB_BASE_URL = "http://$HostAddr`:$Port"

Write-Host "Starting TIA algolib gateway on http://$HostAddr`:$Port ..."
Write-Host "  GET  /algorithms"
Write-Host "  POST /run  -> forwards to 9020-9030 /predict"
Write-Host "Set ALGOLIB_BASE_URL=$env:ALGOLIB_BASE_URL for TIA Agent"

$main = Join-Path $Root "services\tia_algolib_gateway\app\main.py"
& $Python $main
