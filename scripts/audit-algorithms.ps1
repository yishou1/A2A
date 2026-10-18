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
    if ($LASTEXITCODE -ne 0 -or -not $coreId) { throw 'Start the project before auditing algorithms.' }
    # Copy only the read-only diagnostic script so an older running image can be inspected too.
    & docker cp (Join-Path $PSScriptRoot 'audit_algorithm_readiness.py') "${coreId}:/tmp/audit_algorithm_readiness.py"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot stage the read-only audit script.' }
    $containerReport = '/app/.runtime/algorithm-audit/readiness-' + [guid]::NewGuid().ToString('N') + '.json'
    & docker @prefix exec -T a2a-core python /tmp/audit_algorithm_readiness.py --root /app `
        --require-real --output $containerReport
    $gateExit = $LASTEXITCODE
    $reportDir = Join-Path $projectRoot '.runtime'
    New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
    $reportPath = Join-Path $reportDir 'algorithm-audit-readiness.json'
    & docker cp "${coreId}:$containerReport" $reportPath
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy the audit report.' }
    Write-Host "Report: $reportPath"
    if ($gateExit -ne 0) {
        Write-Host 'FAILED: Some algorithms are mock, stub, unavailable, inactive or unverified. See real_readiness_gate.blockers.'
        exit $gateExit
    }
    Write-Host 'Real-execution readiness checks passed. Accuracy still requires labeled evaluation data.'
}
catch {
    Write-Error $_
    exit 1
}
