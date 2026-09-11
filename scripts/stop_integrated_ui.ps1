[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $repoRoot '.runtime\integrated-ui'

foreach ($name in @('amos', 'algolib')) {
    $recordPath = Join-Path $runtimeRoot "$name.json"
    if (-not (Test-Path -LiteralPath $recordPath)) {
        Write-Host "[skip] $name was not started by the integrated launcher"
        continue
    }

    $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Remove-Item -LiteralPath $recordPath -Force
        Write-Host "[stale] $name PID $($record.pid) is no longer running"
        continue
    }

    $expectedStart = [DateTime]::Parse($record.started_at_utc).ToUniversalTime()
    $actualStart = $process.StartTime.ToUniversalTime()
    if ([Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -gt 2) {
        throw "Refusing to stop ${name}: PID $($record.pid) has been reused by another process."
    }

    Stop-Process -Id $process.Id
    $process.WaitForExit(5000) | Out-Null
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
    Remove-Item -LiteralPath $recordPath -Force
    Write-Host "[stopped] $name (PID $($process.Id))"
}
