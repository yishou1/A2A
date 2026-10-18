Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'docker-common.ps1')

try {
    Initialize-A2ADocker
    $project = Get-A2AComposeProjectName -ProjectRoot $projectRoot
    $prefix = @('compose', '--project-name', $project, '--project-directory', $projectRoot,
        '-f', (Join-Path $projectRoot 'compose.yaml'))
    $coreId = (& docker @prefix ps -q a2a-core | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $coreId) { throw 'Start the project before running generic acceptance.' }
    & docker exec $coreId mkdir -p /app/acceptance/public_data
    if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare the acceptance data directory.' }
    $copies = @(
        @((Join-Path $PSScriptRoot 'accept_generic_public_data.py'), '/app/scripts/accept_generic_public_data.py'),
        @((Join-Path $projectRoot 'acceptance/generic_criteria.json'), '/app/acceptance/generic_criteria.json'),
        @((Join-Path $projectRoot 'acceptance/public_data/.'), '/app/acceptance/public_data')
    )
    foreach ($copy in $copies) {
        & docker cp $copy[0] "${coreId}:$($copy[1])"
        if ($LASTEXITCODE -ne 0) { throw "Cannot stage acceptance input: $($copy[0])" }
    }
    $runId = [guid]::NewGuid().ToString('N')
    $containerOutput = '/app/.runtime/algorithm-audit/generic-public-' + $runId
    & docker @prefix exec -T a2a-core python /app/scripts/accept_generic_public_data.py --root /app --output $containerOutput
    $result = $LASTEXITCODE
    $reportRoot = Join-Path $projectRoot '.runtime/generic-acceptance'
    New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
    $reportDir = Join-Path $reportRoot $runId
    & docker cp "${coreId}:$containerOutput" $reportDir
    if ($LASTEXITCODE -ne 0) { throw 'Cannot retrieve a fresh acceptance report.' }
    $report = Join-Path $reportDir 'report.json'
    [IO.File]::WriteAllText((Join-Path $reportRoot 'latest-report.txt'), $report, [Text.UTF8Encoding]::new($false))
    Write-Host "Report: $report"
    Write-Host 'Scope: clustering and weighted averaging on public plant/chemistry data. This is not whole-library or operational accuracy certification.'
    exit $result
}
catch {
    Write-Error $_
    exit 1
}
