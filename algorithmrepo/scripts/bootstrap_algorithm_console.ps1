[CmdletBinding()]
param(
    [string]$RegistryPath,
    [string]$AlgolibPath,
    [switch]$IncludeDraft,
    [switch]$RefreshExisting,
    [switch]$Activate
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($RegistryPath)) {
    $RegistryPath = Join-Path $repoRoot '.algolib\registry.json'
}
if ([string]::IsNullOrWhiteSpace($AlgolibPath)) {
    $realOnnxCli = Join-Path $repoRoot 'build-smoke-offline\Debug\algolib.exe'
    $stubCli = Join-Path $repoRoot 'build\Debug\algolib.exe'
    $AlgolibPath = if (Test-Path -LiteralPath $realOnnxCli) { $realOnnxCli } else { $stubCli }
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
            $existingKeys["$($item.key.algorithm_id)|$($item.key.version)|$($item.key.backend_type)"] = $true
        }
    }

    $registered = 0
    $refreshed = 0
    $skipped = 0
    $failed = @()
    $cards = Get-ChildItem -Path (Join-Path $repoRoot 'examples') -Filter 'algorithm_card.yaml' -Recurse

    foreach ($card in $cards) {
        $content = Get-Content -LiteralPath $card.FullName -Raw -Encoding UTF8
        $backend = Read-CardField -Content $content -Name 'backend_type'
        $cardStatus = Read-CardField -Content $content -Name 'status'
        $algorithmId = Read-CardField -Content $content -Name 'algorithm_id'
        $version = Read-CardField -Content $content -Name 'version'

        # Python HTTP packages require their service to be running during validation.
        # The default console bootstrap therefore imports self-contained ONNX assets only.
        if ($backend -ne 'onnx' -or (-not $IncludeDraft -and $cardStatus -ne 'validated')) {
            continue
        }

        $key = "$algorithmId|$version|$backend"
        if ($existingKeys.ContainsKey($key)) {
            if ($RefreshExisting) {
                $refreshOutput = & $AlgolibPath validate $algorithmId $version $backend 2>&1
                if ($LASTEXITCODE -ne 0) {
                    $failed += [pscustomobject]@{ Algorithm = $algorithmId; Message = ($refreshOutput -join "`n") }
                }
                else {
                    $refreshed++
                }
            }
            $skipped++
            continue
        }

        $output = & $AlgolibPath register $card.Directory.FullName 2>&1
        if ($LASTEXITCODE -ne 0) {
            $failed += [pscustomobject]@{ Algorithm = $algorithmId; Message = ($output -join "`n") }
            continue
        }

        $registered++
        $existingKeys[$key] = $true
        if ($Activate) {
            $activateOutput = & $AlgolibPath activate $algorithmId $version $backend 2>&1
            if ($LASTEXITCODE -ne 0) {
                $failed += [pscustomobject]@{ Algorithm = $algorithmId; Message = ($activateOutput -join "`n") }
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
