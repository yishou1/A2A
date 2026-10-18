[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop', 'Status', 'Check', 'Logs')]
    [string]$Action = 'Start',
    [switch]$Gpu,
    [switch]$NoBrowser,
    [ValidateRange(60, 7200)]
    [int]$StartupTimeoutSeconds = 3600,
    [ValidateRange(1, 65535)]
    [int]$AmosPort,
    [ValidateRange(1, 65535)]
    [int]$GatewayPort
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'docker-common.ps1')

try {
    Set-Location -LiteralPath $projectRoot
    Write-Host "[project] $projectRoot"
    Write-Host "[action] $Action"
    if ($PSBoundParameters.ContainsKey('AmosPort')) { $env:AMOS_HOST_PORT = "$AmosPort" }
    if ($PSBoundParameters.ContainsKey('GatewayPort')) { $env:A2A_GATEWAY_HOST_PORT = "$GatewayPort" }
    Initialize-A2ADocker
    switch ($Action) {
        { $_ -in @('Start', 'Check') } {
            & (Join-Path $PSScriptRoot 'docker-start.ps1') -Gpu:$Gpu -NoBrowser:$NoBrowser `
                -StartupTimeoutSeconds $StartupTimeoutSeconds -CheckOnly:($Action -eq 'Check')
            exit $LASTEXITCODE
        }
        'Stop' {
            & (Join-Path $PSScriptRoot 'docker-stop.ps1')
            exit $LASTEXITCODE
        }
        'Logs' {
            & (Join-Path $PSScriptRoot 'docker-compose.ps1') logs --tail=100 qwen-init ollama synapserag a2a-core amos
            exit $LASTEXITCODE
        }
        'Status' {
            $project = Get-A2AComposeProjectName -ProjectRoot $projectRoot
            $prefix = @('compose', '--project-name', $project, '--project-directory', $projectRoot,
                '-f', (Join-Path $projectRoot 'compose.yaml'))
            & docker @prefix ps --all
            if ($LASTEXITCODE -ne 0) { throw 'Could not read project status. Start Docker Desktop first.' }
            & docker @prefix exec -T a2a-core python scripts/docker/healthcheck.py full
            if ($LASTEXITCODE -ne 0) { throw 'The full project is not ready. Use -Action Logs for details, or -Action Start to start it.' }
            Write-Host 'Full project health checks passed.'
            exit 0
        }
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
