Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'docker-common.ps1')
$composeFile = Join-Path $projectRoot 'compose.yaml'
$composeProject = Get-A2AComposeProjectName -ProjectRoot $projectRoot
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine('Docker CLI was not found.')
    exit 1
}
if (-not (Test-Path -LiteralPath $composeFile)) {
    [Console]::Error.WriteLine("Compose file not found: $composeFile")
    exit 1
}

Set-Location -LiteralPath $projectRoot
& docker compose --project-name $composeProject --project-directory $projectRoot -f $composeFile down
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine("docker compose down failed (exit code $LASTEXITCODE).")
    exit $LASTEXITCODE
}
Write-Host 'Stopped. Model and runtime data volumes were preserved.'
