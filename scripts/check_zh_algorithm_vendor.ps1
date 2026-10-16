[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$canonical = Join-Path $repoRoot "algorithmrepo\services\a2a_algorithms_common"
$vendored = Join-Path $repoRoot "commander\services\a2a_algorithms_common"
$sharedFiles = @(
    "algorithm_profiles.py",
    "association_rules.py",
    "execution_planner.py",
    "motion_prediction.py",
    "mission_feature_adapter.py",
    "mission_feature_schema.py",
    "mission_scorer.py",
    "pickle_compat.py",
    "closed_loop_advisor.py"
    "distributed_cbba.py"
)

$different = @()
foreach ($file in $sharedFiles) {
    $canonicalPath = Join-Path $canonical $file
    $vendoredPath = Join-Path $vendored $file
    if (-not (Test-Path -LiteralPath $canonicalPath) -or -not (Test-Path -LiteralPath $vendoredPath)) {
        $different += $file
        continue
    }
    $canonicalText = ((Get-Content -LiteralPath $canonicalPath -Raw) -replace "`r`n", "`n").TrimEnd()
    $vendoredText = ((Get-Content -LiteralPath $vendoredPath -Raw) -replace "`r`n", "`n").TrimEnd()
    if ($canonicalText -cne $vendoredText) {
        $different += $file
    }
}

if ($different.Count -gt 0) {
    Write-Error ("Algorithm vendor drift detected: " + ($different -join ", "))
}

Write-Host "Execution-control and closed-loop algorithm sources are in sync."
