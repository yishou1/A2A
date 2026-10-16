[CmdletBinding()]
param(
    [string]$RegistryPath,
    [string]$AlgolibPath,
    [switch]$IncludeDraft,
    [switch]$IncludePythonHttp,
    [string[]]$OnlyAlgorithmId,
    [switch]$RefreshExisting,
    [switch]$Activate
)

$ErrorActionPreference = 'Stop'

$algorithmRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$repoRoot = (Resolve-Path (Join-Path $algorithmRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($RegistryPath)) {
    $RegistryPath = Join-Path $repoRoot '.algolib\registry.json'
}
if ([string]::IsNullOrWhiteSpace($AlgolibPath)) {
    $candidates = @(
        (Join-Path $algorithmRoot 'build-runtime\Debug\algolib.exe'),
        (Join-Path $algorithmRoot 'build-smoke-offline\Debug\algolib.exe'),
        (Join-Path $repoRoot 'build-smoke-offline\Debug\algolib.exe'),
        (Join-Path $algorithmRoot 'build\Debug\algolib.exe'),
        (Join-Path $repoRoot 'build\Debug\algolib.exe')
    )
    $AlgolibPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}

if (-not (Test-Path -LiteralPath $AlgolibPath)) {
    throw "AlgoLib CLI not found: $AlgolibPath. Build algolib before bootstrapping the console."
}

$registryDirectory = Split-Path -Parent $RegistryPath
if (-not (Test-Path -LiteralPath $registryDirectory)) {
    New-Item -ItemType Directory -Path $registryDirectory | Out-Null
}

function Read-CardField {
    param([string]$Content, [string]$Name)
    $match = [regex]::Match($Content, "(?m)^$([regex]::Escape($Name)):\s*([^\s#]+)")
    if (-not $match.Success) { return '' }
    return $match.Groups[1].Value.Trim('"', "'")
}

$previousRegistryPath = $env:ALGOLIB_REGISTRY_PATH
$env:ALGOLIB_REGISTRY_PATH = [System.IO.Path]::GetFullPath($RegistryPath)

try {
    $existingKeys = @{}
    if (Test-Path -LiteralPath $env:ALGOLIB_REGISTRY_PATH) {
        $registryDocument = Get-Content -LiteralPath $env:ALGOLIB_REGISTRY_PATH -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($item in @($registryDocument.entries)) {
            $existingKeys["$($item.key.algorithm_id)|$($item.key.version)|$($item.key.backend_type)"] = [string]$item.status
        }
    }

    $registered = 0
    $refreshed = 0
    $skipped = 0
    $failed = @()
    $cards = Get-ChildItem -Path (Join-Path $algorithmRoot 'examples') -Filter 'algorithm_card.yaml' -Recurse

    foreach ($card in $cards) {
        $content = Get-Content -LiteralPath $card.FullName -Raw -Encoding UTF8
        $backend = Read-CardField -Content $content -Name 'backend_type'
        $cardStatus = Read-CardField -Content $content -Name 'status'
        $cardAlgorithmId = Read-CardField -Content $content -Name 'algorithm_id'
        $version = Read-CardField -Content $content -Name 'version'

        if ($OnlyAlgorithmId.Count -gt 0 -and $cardAlgorithmId -notin $OnlyAlgorithmId) {
            continue
        }
        # Python HTTP packages require their service to be running during validation,
        # so they remain opt-in while self-contained ONNX packages are the default.
        if ($backend -ne 'onnx' -and -not ($IncludePythonHttp -and $backend -eq 'python_http_service')) {
            continue
        }
        if (-not $IncludeDraft -and $cardStatus -ne 'validated') {
            continue
        }

        $key = "$cardAlgorithmId|$version|$backend"
        if ($existingKeys.ContainsKey($key)) {
            $existingStatus = $existingKeys[$key]
            if ($RefreshExisting) {
                $refreshOutput = & $AlgolibPath validate $cardAlgorithmId $version $backend 2>&1
                if ($LASTEXITCODE -ne 0) {
                    $failed += [pscustomobject]@{ Algorithm = $cardAlgorithmId; Message = ($refreshOutput -join "`n") }
                }
                else {
                    $refreshed++
                }
            }
            if ($Activate -and $existingStatus -ne 'active') {
                $activateOutput = & $AlgolibPath activate $cardAlgorithmId $version $backend 2>&1
                if ($LASTEXITCODE -ne 0) {
                    $failed += [pscustomobject]@{ Algorithm = $cardAlgorithmId; Message = ($activateOutput -join "`n") }
                }
            }
            $skipped++
            continue
        }

        $output = & $AlgolibPath register $card.Directory.FullName 2>&1
        if ($LASTEXITCODE -ne 0) {
            $failed += [pscustomobject]@{ Algorithm = $cardAlgorithmId; Message = ($output -join "`n") }
            continue
        }

        $registered++
        $existingKeys[$key] = $true
        if ($Activate) {
            $activateOutput = & $AlgolibPath activate $cardAlgorithmId $version $backend 2>&1
            if ($LASTEXITCODE -ne 0) {
                $failed += [pscustomobject]@{ Algorithm = $cardAlgorithmId; Message = ($activateOutput -join "`n") }
            }
        }
    }

    [pscustomobject]@{
        RegistryPath = $env:ALGOLIB_REGISTRY_PATH
        Registered = $registered
        Refreshed = $refreshed
        ExistingSkipped = $skipped
        Failed = $failed.Count
        Activated = [bool]$Activate
    } | Format-List

    if ($failed.Count -gt 0) {
        Write-Warning 'Some packages could not be registered:'
        $failed | Format-Table -Wrap
        exit 1
    }
}
finally {
    $env:ALGOLIB_REGISTRY_PATH = $previousRegistryPath
}
