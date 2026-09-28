param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Model', 'Runtime', 'All')]
    [string]$Target
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'docker-common.ps1')
$composeFile = Join-Path $projectRoot 'compose.yaml'
$composeProject = Get-A2AComposeProjectName -ProjectRoot $projectRoot
$composePrefix = @('compose', '--project-name', $composeProject, '--project-directory', $projectRoot, '-f', $composeFile)

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI was not found.'
}
$configJson = & docker @composePrefix config --format json
if ($LASTEXITCODE -ne 0) {
    throw 'Cannot resolve the Compose project and its volume names.'
}
$config = $configJson | ConvertFrom-Json
$projectName = [string]$config.name
if (-not $projectName) {
    throw 'Compose returned an empty project name.'
}

$volumeKeys = switch ($Target) {
    'Model' { @('ollama_models') }
    'Runtime' { @('a2a_runtime', 'a2a_state', 'amos_instance', 'hf_cache', 'synapserag_data') }
    'All' { @('ollama_models', 'a2a_runtime', 'a2a_state', 'amos_instance', 'hf_cache', 'synapserag_data') }
}

Set-Location -LiteralPath $projectRoot
& docker @composePrefix down
if ($LASTEXITCODE -ne 0) {
    throw 'Could not stop this Compose project.'
}
foreach ($volumeKey in $volumeKeys) {
    $volumeName = [string]$config.volumes.$volumeKey.name
    if (-not $volumeName) {
        throw "Compose returned no name for volume $volumeKey."
    }
    $inspectJson = $null
    $inspectExitCode = 1
    try {
        $inspectJson = & docker volume inspect $volumeName 2>$null
        $inspectExitCode = $LASTEXITCODE
    }
    catch {
        $inspectExitCode = 1
    }
    if ($inspectExitCode -ne 0) {
        Write-Host "Already absent: $volumeName"
        continue
    }
    $inspect = @($inspectJson | ConvertFrom-Json)[0]
    if ($inspect.Name -ne $volumeName -or
        $inspect.Labels.'com.docker.compose.project' -ne $projectName -or
        $inspect.Labels.'com.docker.compose.volume' -ne $volumeKey) {
        throw "Refusing to remove volume with unexpected identity: $volumeName"
    }
    & docker volume rm $volumeName
    if ($LASTEXITCODE -ne 0) {
        throw "Could not remove volume $volumeName."
    }
}
Write-Host "Reset complete: $Target. The Compose project is stopped."
