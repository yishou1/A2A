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
Set-Location -LiteralPath $projectRoot
$composeCommandArgs = @($args)
$buildRequested = ($composeCommandArgs.Count -gt 0 -and $composeCommandArgs[0] -ieq 'build') -or
    ($composeCommandArgs -contains '--build')
if ($buildRequested) {
    $buildContext = New-A2AComposeBuildContext -ProjectRoot $projectRoot
    try {
        $composePrefix = @('compose', '--project-name', $composeProject, '--project-directory', $buildContext.Path,
            '-f', (Join-Path $buildContext.Path 'compose.yaml'))
        & docker @composePrefix @composeCommandArgs
        $composeExitCode = $LASTEXITCODE
    }
    finally {
        Remove-A2AComposeBuildContext -Context $buildContext
    }
    exit $composeExitCode
}

& docker compose --project-name $composeProject --project-directory $projectRoot -f $composeFile @composeCommandArgs
exit $LASTEXITCODE
