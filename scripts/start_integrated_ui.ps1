[CmdletBinding()]
param(
    [string]$Python,
    [string]$AlgolibServer,
    [string]$AlgolibCli,
    [ValidateRange(5, 300)]
    [int]$StartupTimeoutSeconds = 60,
    [switch]$SkipWebBuild,
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$algorithmRoot = Join-Path $repoRoot 'algorithmrepo'
$webRoot = Join-Path $algorithmRoot 'web'
$amosRoot = Join-Path $repoRoot 'amos-platform'
$runtimeRoot = Join-Path $repoRoot '.runtime\integrated-ui'
$logRoot = Join-Path $runtimeRoot 'logs'
$registryRoot = Join-Path $algorithmRoot '.algolib'
$registryPath = Join-Path $registryRoot 'registry.json'
$executionLogPath = Join-Path $registryRoot 'executions.jsonl'
$functionCatalogPath = Join-Path $algorithmRoot 'config\operational_function_catalog.yaml'

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
New-Item -ItemType Directory -Path $registryRoot -Force | Out-Null

function Resolve-FirstFile {
    param(
        [string]$Configured,
        [string[]]$Candidates,
        [string]$Description
    )
    if (-not [string]::IsNullOrWhiteSpace($Configured)) {
        if (Test-Path -LiteralPath $Configured -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Configured).Path
        }
        throw "$Description not found: $Configured"
    }
    foreach ($candidate in $Candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "$Description not found. Checked: $($Candidates -join ', ')"
}

function Test-HttpReady {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Wait-HttpReady {
    param(
        [string]$Name,
        [string]$Url
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-HttpReady -Url $Url) { return }
        Start-Sleep -Milliseconds 400
    }
    throw "$Name did not become ready at $Url within $StartupTimeoutSeconds seconds."
}

function Start-ManagedService {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$HealthUrl
    )
    if (Test-HttpReady -Url $HealthUrl) {
        Write-Host "[running] $Name at $HealthUrl"
        return
    }

    $stdoutPath = Join-Path $logRoot "$Name.out.log"
    $stderrPath = Join-Path $logRoot "$Name.err.log"
    $recordPath = Join-Path $runtimeRoot "$Name.json"
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    @{
        pid = $process.Id
        started_at_utc = $process.StartTime.ToUniversalTime().ToString('o')
        executable = $FilePath
        health_url = $HealthUrl
    } | ConvertTo-Json | Set-Content -LiteralPath $recordPath -Encoding UTF8

    try {
        Wait-HttpReady -Name $Name -Url $HealthUrl
    }
    catch {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
        $details = if (Test-Path -LiteralPath $stderrPath) {
            (Get-Content -LiteralPath $stderrPath -Tail 20) -join [Environment]::NewLine
        } else { '' }
        throw "$($_.Exception.Message)`n$details"
    }
    Write-Host "[started] $Name (PID $($process.Id))"
}

$pythonCandidates = @(
    (Join-Path $amosRoot '.venv\Scripts\python.exe'),
    (Join-Path $repoRoot '.venv\Scripts\python.exe')
)
$pythonPath = Resolve-FirstFile -Configured $Python -Candidates $pythonCandidates -Description 'AMOS Python'

$serverCandidates = @(
    (Join-Path $algorithmRoot 'build-smoke-offline\Debug\algolib_server.exe'),
    (Join-Path $algorithmRoot 'build\Debug\algolib_server.exe'),
    (Join-Path $repoRoot 'build-smoke-offline\Debug\algolib_server.exe'),
    (Join-Path $repoRoot 'build\Debug\algolib_server.exe')
)
$serverPath = Resolve-FirstFile -Configured $AlgolibServer -Candidates $serverCandidates -Description 'AlgoLib Server'

if (-not $SkipWebBuild) {
    if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'node_modules'))) {
        throw "Frontend dependencies are missing. Run 'npm ci' in $webRoot first."
    }
    Write-Host '[build] AlgoLib management console'
    Push-Location $webRoot
    try {
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'AlgoLib frontend build failed.' }
    }
    finally {
        Pop-Location
    }
}
elseif (-not (Test-Path -LiteralPath (Join-Path $webRoot 'dist\index.html'))) {
    throw 'AlgoLib frontend dist is missing; remove -SkipWebBuild or run npm run build.'
}

if (-not (Test-Path -LiteralPath $registryPath)) {
    $cliCandidates = @(
        (Join-Path $algorithmRoot 'build-smoke-offline\Debug\algolib.exe'),
        (Join-Path $algorithmRoot 'build\Debug\algolib.exe'),
        (Join-Path $repoRoot 'build-smoke-offline\Debug\algolib.exe'),
        (Join-Path $repoRoot 'build\Debug\algolib.exe')
    )
    $cliPath = Resolve-FirstFile -Configured $AlgolibCli -Candidates $cliCandidates -Description 'AlgoLib CLI'
    Write-Host '[registry] registering and activating validated ONNX packages'
    & (Join-Path $algorithmRoot 'scripts\bootstrap_algorithm_console.ps1') `
        -AlgolibPath $cliPath `
        -RegistryPath $registryPath `
        -Activate
    if ($LASTEXITCODE -ne 0) { throw 'AlgoLib registry bootstrap failed.' }
}

Start-ManagedService `
    -Name 'algolib' `
    -FilePath $serverPath `
    -ArgumentList @(
        '--host', '127.0.0.1',
        '--port', '8088',
        '--registry', $registryPath,
        '--execution-log', $executionLogPath,
        '--function-catalog', $functionCatalogPath
    ) `
    -WorkingDirectory $algorithmRoot `
    -HealthUrl 'http://127.0.0.1:8088/health'

$env:ALGOLIB_API_URL = 'http://127.0.0.1:8088'
$env:ALGOLIB_CONSOLE_URL = '/algolib/algorithms'
Start-ManagedService `
    -Name 'amos' `
    -FilePath $pythonPath `
    -ArgumentList @(
        '-m', 'amos_platform.api.app_factory',
        '--host', '127.0.0.1',
        '--port', '5000'
    ) `
    -WorkingDirectory $repoRoot `
    -HealthUrl 'http://127.0.0.1:5000/'

Write-Host ''
Write-Host 'Integrated UI is ready:'
Write-Host '  AMOS:    http://127.0.0.1:5000/'
Write-Host '  AlgoLib: http://127.0.0.1:5000/algolib/algorithms'
Write-Host "  Logs:    $logRoot"

if ($OpenBrowser) {
    Start-Process 'http://127.0.0.1:5000/'
}
