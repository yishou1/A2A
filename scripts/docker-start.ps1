param(
    [switch]$NoBrowser,
    [switch]$Gpu,
    [ValidateRange(60, 7200)]
    [int]$StartupTimeoutSeconds = 3600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'docker-common.ps1')
$composeFile = Join-Path $projectRoot 'compose.yaml'
$composeProject = Get-A2AComposeProjectName -ProjectRoot $projectRoot
$composePrefix = @('compose', '--project-name', $composeProject, '--project-directory', $projectRoot, '-f', $composeFile)
if ($Gpu) {
    $composePrefix += @('-f', (Join-Path $projectRoot 'compose.gpu.yaml'))
}

function Invoke-Compose {
    param([string[]]$CommandArgs)
    & docker @composePrefix @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($CommandArgs -join ' ') failed (exit code $LASTEXITCODE)."
    }
}

function Get-DockerServerOS {
    try {
        $value = & docker info --format '{{.OSType}}' 2>$null
        if ($LASTEXITCODE -ne 0) {
            return ''
        }
        return ($value | Out-String).Trim()
    }
    catch {
        return ''
    }
}

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker CLI was not found. Install Docker Desktop first.'
    }
    if (-not (Test-Path -LiteralPath $composeFile)) {
        throw "Compose file not found: $composeFile"
    }

    & docker compose version
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose is not available. Update Docker Desktop.'
    }

    $serverOS = Get-DockerServerOS
    if (-not $serverOS) {
        Write-Host '[docker] Starting Docker Desktop...'
        & docker desktop start --timeout 180
        if ($LASTEXITCODE -ne 0) {
            throw 'Docker Desktop could not be started. Start it manually and select Linux containers.'
        }
        $deadline = (Get-Date).AddSeconds(180)
        do {
            Start-Sleep -Seconds 3
            $serverOS = Get-DockerServerOS
        } while (-not $serverOS -and (Get-Date) -lt $deadline)
    }
    if ($serverOS -ne 'linux') {
        throw "Docker is using '$serverOS' containers. Switch Docker Desktop to Linux containers."
    }
    $memoryText = (& docker info --format '{{.MemTotal}}' 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $memoryText -match '^\d+$') {
        $memoryGiB = [math]::Round(([double]$memoryText / 1GB), 1)
        # A2A's agent and algorithm processes use about 5 GiB before Ollama
        # loads Qwen.  An 8 GB Docker allocation leaves too little headroom
        # for a full-context request and llama.cpp's prompt cache.  Docker
        # reports slightly less than the configured limit, so 11.5 GiB is the
        # observable threshold for a 12 GB Desktop/WSL allocation.
        if ($memoryGiB -lt 11.5) {
            throw "Docker Desktop exposes only $memoryGiB GiB RAM. Allocate at least 12 GB before starting A2A and Qwen."
        }
    }

    Set-Location -LiteralPath $projectRoot
    Write-Host '[1/4] Validating Compose configuration...'
    Invoke-Compose -CommandArgs @('config', '--quiet')

    Write-Host '[2/4] Building application images (first run requires network access)...'
    $buildContext = New-A2AComposeBuildContext -ProjectRoot $projectRoot
    try {
        $buildPrefix = @('compose', '--project-name', $composeProject, '--project-directory', $buildContext.Path,
            '-f', (Join-Path $buildContext.Path 'compose.yaml'))
        if ($Gpu) {
            $buildPrefix += @('-f', (Join-Path $buildContext.Path 'compose.gpu.yaml'))
        }
        & docker @buildPrefix build
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose build failed (exit code $LASTEXITCODE)."
        }
    }
    finally {
        Remove-A2AComposeBuildContext -Context $buildContext
    }

    Write-Host '[3/4] Starting infrastructure and preparing Qwen chat + embedding models...'
    # A configuration change may recreate Ollama while an older A2A container
    # is still using most of Docker Desktop's memory. Stop application services
    # first so the required CPU model can cold-load and pass its probe reliably.
    Invoke-Compose -CommandArgs @('stop', 'amos', 'a2a-core', 'synapserag')
    Invoke-Compose -CommandArgs @('up', '-d', 'ollama', 'nacos', 'auth-mock')
    Invoke-Compose -CommandArgs @('up', '--force-recreate', '--no-deps', '--exit-code-from', 'qwen-init', 'qwen-init')

    Write-Host '[4/4] Starting SynapseRAG, A2A and AMOS; waiting for healthy services...'
    Invoke-Compose -CommandArgs @('up', '-d', '--wait', '--wait-timeout', "$StartupTimeoutSeconds", 'synapserag', 'a2a-core', 'amos')
    Invoke-Compose -CommandArgs @('exec', '-T', 'a2a-core', 'python', 'scripts/docker/healthcheck.py', 'full')

    $amosEndpoint = (& docker @composePrefix port amos 5000 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $amosEndpoint) {
        throw 'AMOS is healthy, but its published Windows port could not be resolved.'
    }
    $amosUrl = "http://$amosEndpoint/"
    Write-Host "Ready: $amosUrl"
    if (-not $NoBrowser) {
        Start-Process $amosUrl
    }
    exit 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    $composeHelper = Join-Path $PSScriptRoot 'docker-compose.ps1'
    Write-Host "Inspect logs with: powershell -File `"$composeHelper`" logs --tail=100 qwen-init ollama synapserag a2a-core amos"
    exit 1
}
