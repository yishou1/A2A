param(
    [string]$BundlePath = "",
    [bool]$IncludeLast = $true,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$AlgorithmRoot = Split-Path -Parent $PSScriptRoot
$RepositoryRoot = Split-Path -Parent $AlgorithmRoot
$WorkspaceParent = Split-Path -Parent $RepositoryRoot
$BundleName = "unit17_ap_finetune_upload_20260916"

if ([string]::IsNullOrWhiteSpace($BundlePath)) {
    $BundlePath = Join-Path $WorkspaceParent "release_bundles\$BundleName"
}

if (-not (Test-Path -LiteralPath $BundlePath -PathType Container)) {
    throw "RT-DETR release bundle not found: $BundlePath"
}

$BundlePath = (Resolve-Path -LiteralPath $BundlePath).Path
$ManifestPath = Join-Path $BundlePath "MANIFEST.json"
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Release manifest not found: $ManifestPath"
}

$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Manifest.bundle -ne $BundleName) {
    throw "Unexpected release bundle '$($Manifest.bundle)'; expected '$BundleName'."
}

function Get-ManifestEntry {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalized = $RelativePath.Replace("\", "/")
    $entry = $Manifest.files | Where-Object { $_.path -eq $normalized } | Select-Object -First 1
    if ($null -eq $entry) {
        throw "Manifest entry not found: $normalized"
    }
    return $entry
}

function Assert-SourceArtifact {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $entry = Get-ManifestEntry -RelativePath $RelativePath
    $source = Join-Path $BundlePath $RelativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Release artifact not found: $source"
    }
    $item = Get-Item -LiteralPath $source
    if ($item.Length -ne [long]$entry.bytes) {
        throw "Size mismatch for ${RelativePath}: actual=$($item.Length), expected=$($entry.bytes)"
    }
    $actualHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $entry.sha256.ToLowerInvariant()) {
        throw "SHA-256 mismatch for ${RelativePath}: actual=$actualHash, expected=$($entry.sha256)"
    }
    return [PSCustomObject]@{
        Source = $source
        Hash = $actualHash
        Bytes = $item.Length
    }
}

function Install-ArtifactCopy {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $artifact = Assert-SourceArtifact -RelativePath $RelativePath
    $destinationDirectory = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null

    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        $existingHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -eq $artifact.Hash) {
            Write-Host "Already installed: $Destination"
            return $artifact.Hash
        }
        if (-not $Force) {
            throw "Refusing to overwrite an artifact with a different hash: $Destination (use -Force after reviewing it)"
        }
        Remove-Item -LiteralPath $Destination -Force
    }

    Copy-Item -LiteralPath $artifact.Source -Destination $Destination
    $installedHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($installedHash -ne $artifact.Hash) {
        throw "Installed artifact failed SHA-256 verification: $Destination"
    }
    Write-Host "Installed: $Destination"
    return $installedHash
}

function Install-ArtifactAlias {
    param(
        [Parameter(Mandatory = $true)][string]$Canonical,
        [Parameter(Mandatory = $true)][string]$Alias,
        [Parameter(Mandatory = $true)][string]$ExpectedHash
    )

    $aliasDirectory = Split-Path -Parent $Alias
    New-Item -ItemType Directory -Path $aliasDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $Alias -PathType Leaf) {
        $existingHash = (Get-FileHash -LiteralPath $Alias -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -eq $ExpectedHash) {
            Write-Host "Already installed: $Alias"
            return
        }
        if (-not $Force) {
            throw "Refusing to overwrite an alias with a different hash: $Alias (use -Force after reviewing it)"
        }
        Remove-Item -LiteralPath $Alias -Force
    }

    try {
        New-Item -ItemType HardLink -Path $Alias -Target $Canonical -ErrorAction Stop | Out-Null
        Write-Host "Linked: $Alias"
    }
    catch {
        Copy-Item -LiteralPath $Canonical -Destination $Alias
        Write-Warning "Hard-link creation failed; copied the artifact instead: $Alias"
    }

    $aliasHash = (Get-FileHash -LiteralPath $Alias -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($aliasHash -ne $ExpectedHash) {
        throw "Installed alias failed SHA-256 verification: $Alias"
    }
}

$CheckpointDirectory = Join-Path $AlgorithmRoot "models\checkpoints"
$BestCheckpoint = Join-Path $CheckpointDirectory "battlefield_rtdetr_unit17_coco25_ap_kaggle_best.pt"
$BestHash = Install-ArtifactCopy `
    -RelativePath "01_checkpoints\battlefield_rtdetr_unit17_coco25_ap_kaggle_best.pt" `
    -Destination $BestCheckpoint

Install-ArtifactAlias `
    -Canonical $BestCheckpoint `
    -Alias (Join-Path $CheckpointDirectory "battlefield_rtdetr.pt") `
    -ExpectedHash $BestHash

if ($IncludeLast) {
    Install-ArtifactCopy `
        -RelativePath "01_checkpoints\battlefield_rtdetr_unit17_coco25_last.pt" `
        -Destination (Join-Path $CheckpointDirectory "battlefield_rtdetr_unit17_coco25_last.pt") | Out-Null
}

$OnnxModel = Join-Path $AlgorithmRoot "models\battlefield_rtdetr.onnx"
$OnnxHash = Install-ArtifactCopy `
    -RelativePath "02_onnx\battlefield_rtdetr.onnx" `
    -Destination $OnnxModel

$OnnxPackageModel = Join-Path $AlgorithmRoot "examples\battlefield_rtdetr_detector_onnx\1.0.0\model.onnx"
Install-ArtifactAlias `
    -Canonical $OnnxModel `
    -Alias $OnnxPackageModel `
    -ExpectedHash $OnnxHash

Write-Host ""
Write-Host "RT-DETR release artifacts are ready."
Write-Host "PT SHA-256:   $BestHash"
Write-Host "ONNX SHA-256: $OnnxHash"
Write-Host "The large binaries are intentionally ignored by Git."
