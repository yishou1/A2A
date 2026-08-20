<#
.SYNOPSIS
  Local URI demo without MinIO: local:// input -> TIA -> annotated artifacts.

.DESCRIPTION
  Mode Http (default): POST /sendMessage to a running TIA agent.
  Mode LocalPipeline: run Python pipeline directly (no HTTP server).

  Terminal 1 (Http mode):
    cd d:\a2a_project\A2A-main
    $env:PYTHONPATH="."
    $env:TIA_CONFIG="config\default.yaml"
    $env:TIA_ALLOW_LOCAL_FILE="1"
    $env:TIA_ARTIFACT_ENABLED="1"
    $env:TIA_NACOS_REGISTER="0"
    $env:TIA_SKIP_WARMUP="1"
    python tactical_intelligence_agent\main.py

  Terminal 2:
    .\scripts\demo_local_uri_sendmessage.ps1
    .\scripts\demo_local_uri_sendmessage.ps1 -ImagePath "runs\detect\battlefield_rtdetr\val_batch0_pred.jpg"
    .\scripts\demo_local_uri_sendmessage.ps1 -Mode LocalPipeline
#>

[CmdletBinding()]
param(
    [string]$ImagePath = "",
    [string]$TiaHost = "127.0.0.1",
    [int]$Port = 8015,
    [string]$BearerToken = "mock-jwt-token-abcd",
    [string]$MissionId = "wf-local-uri-demo",
    [ValidateSet("Http", "LocalPipeline")]
    [string]$Mode = "Http",
    [double]$PlatformLat = 30.512,
    [double]$PlatformLon = 114.381,
    [double]$AltitudeM = 3200.0
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$PythonExe = "python"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VenvPython) {
    $PythonExe = $VenvPython
}

function Resolve-ProjectImage {
    param([string]$Candidate)

    $candidates = @()
    if ($Candidate) {
        $candidates += $Candidate
    } else {
        $candidates += @(
            "datasets\battlefield\images\val\P0002.png",
            "datasets\battlefield\images\val\P0041.png",
            "runs\detect\battlefield_rtdetr\val_batch0_pred.jpg",
            "runs\detect\battlefield_rtdetr\val_batch0_labels.jpg"
        )
    }

    foreach ($rel in $candidates) {
        $path = if ([System.IO.Path]::IsPathRooted($rel)) { $rel } else { Join-Path $Root $rel }
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            return (Resolve-Path -LiteralPath $path).Path
        }
    }

    throw "Image not found. Pass -ImagePath, e.g. runs\detect\battlefield_rtdetr\val_batch0_pred.jpg"
}

function Convert-ToLocalUri {
    param([string]$AbsolutePath)
    $normalized = ($AbsolutePath -replace "\\", "/")
    return "local:///$normalized"
}

function Get-Sha256Hex {
    param([string]$FilePath)
    $hash = Get-FileHash -LiteralPath $FilePath -Algorithm SHA256
    return $hash.Hash.ToLowerInvariant()
}

function Get-ImageMimeType {
    param([string]$FilePath)
    switch ([System.IO.Path]::GetExtension($FilePath).ToLowerInvariant()) {
        ".png" { return "image/png" }
        ".jpg" { return "image/jpeg" }
        ".jpeg" { return "image/jpeg" }
        ".webp" { return "image/webp" }
        default { return "application/octect-stream" }
    }
}

function Set-TiaEnv {
    $env:PYTHONPATH = $Root
    $env:TIA_CONFIG = Join-Path $Root "config\default.yaml"
    $env:TIA_ALLOW_LOCAL_FILE = "1"
    $env:TIA_ARTIFACT_ENABLED = "1"
    $env:TIA_NACOS_REGISTER = "0"
    if (-not $env:TIA_SKIP_WARMUP) {
        $env:TIA_SKIP_WARMUP = "1"
    }
}

function Invoke-LocalPipeline {
    param(
        [string]$ImageFile,
        [string]$OutputPrefixUri
    )

    Set-TiaEnv
    $pyArgs = @(
        "scripts/demo_local_uri_pipeline.py",
        "--image", $ImageFile,
        "--mission-id", $MissionId,
        "--platform-lat", $PlatformLat,
        "--platform-lon", $PlatformLon,
        "--altitude-m", $AltitudeM,
        "--output-prefix", $OutputPrefixUri
    )

    Write-Host ""
    Write-Host "==> Local pipeline (no HTTP agent)" -ForegroundColor Cyan
    & $PythonExe @pyArgs
    if ($LASTEXITCODE -ne 0) {
        throw "demo_local_uri_pipeline.py exited with code $LASTEXITCODE"
    }
}

function Invoke-SendMessage {
    param(
        [hashtable]$Payload,
        [string]$BaseUrl,
        [string]$Token
    )

    $json = $Payload | ConvertTo-Json -Depth 12 -Compress
    $headers = @{
        Authorization = "Bearer $Token"
        "Content-Type" = "application/json"
    }

    Write-Host ""
    Write-Host "==> POST $BaseUrl/sendMessage" -ForegroundColor Cyan
    Write-Host "Request body:" -ForegroundColor DarkGray
    Write-Host ($Payload | ConvertTo-Json -Depth 12)

    try {
        return Invoke-RestMethod -Uri "$BaseUrl/sendMessage" -Method Post -Headers $headers -Body $json -TimeoutSec 600
    } catch {
        if ($_.Exception.Response) {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $body = $reader.ReadToEnd()
            throw "HTTP request failed: $($_.Exception.Message)`n$body"
        }
        throw
    }
}

function Save-IntelligencePacket {
    param(
        $Packet,
        [string]$MissionId,
        [string]$OutputRoot
    )

    if (-not $Packet) {
        Write-Host "[WARN] No intelligence_packet in response; skip save" -ForegroundColor Yellow
        return
    }

    $packetId = $Packet.packet_id
    if (-not $packetId) {
        Write-Host "[WARN] intelligence_packet missing packet_id; skip save" -ForegroundColor Yellow
        return
    }

    $missionDir = Join-Path $OutputRoot ($MissionId -replace "/", "_")
    New-Item -ItemType Directory -Force -Path $missionDir | Out-Null

    $packetPath = Join-Path $missionDir "$packetId.json"
    $latestPath = Join-Path $missionDir "latest.json"
    $json = $Packet | ConvertTo-Json -Depth 20
    Set-Content -LiteralPath $packetPath -Value $json -Encoding UTF8
    Set-Content -LiteralPath $latestPath -Value $json -Encoding UTF8

    Write-Host ""
    Write-Host "Saved intelligence packet:" -ForegroundColor Green
    Write-Host "  $packetPath"
    Write-Host "  $latestPath"
}

function Show-ResultSummary {
    param($Response, [string]$ArtifactStagingRoot)

    Write-Host ""
    Write-Host "==> Response summary" -ForegroundColor Green
    Write-Host "status       : $($Response.status)"
    Write-Host "work_item    : $($Response.work_item)"
    Write-Host "role         : $($Response.role)"
    Write-Host "target_count : $($Response.output.target_count)"
    Write-Host "summary      : $($Response.output.summary)"

    $attachments = @($Response.output.output_attachments)
    if ($attachments.Count -eq 0) {
        Write-Host ""
        Write-Host "[WARN] output_attachments is empty. Start agent with TIA_ARTIFACT_ENABLED=1" -ForegroundColor Yellow
        $staging = Join-Path $ArtifactStagingRoot $MissionId
        if (Test-Path -LiteralPath $staging) {
            Write-Host "Local staging dir: $staging" -ForegroundColor Yellow
            Get-ChildItem -LiteralPath $staging | ForEach-Object { Write-Host "  - $($_.FullName)" }
        }
        return
    }

    Write-Host ""
    Write-Host "Artifact URI / local path:" -ForegroundColor Green
    foreach ($att in $attachments) {
        Write-Host "  uri             : $($att.uri)"
        Write-Host "  detection_count : $($att.meta.detection_count)"
        if ($att.meta.local_staging_path) {
            Write-Host "  local_staging   : $($att.meta.local_staging_path)" -ForegroundColor Cyan
        }
        Write-Host ""
    }
}

# --- main ---

$imageFile = Resolve-ProjectImage -Candidate $ImagePath
$imageUri = Convert-ToLocalUri -AbsolutePath $imageFile
$checksum = Get-Sha256Hex -FilePath $imageFile
$mimeType = Get-ImageMimeType -FilePath $imageFile
$outputPrefix = Convert-ToLocalUri -AbsolutePath (Join-Path $Root "data\output\processed")
$workItem = "${MissionId}:local-uri-demo"
$artifactStaging = Join-Path $Root "data\output\artifacts"

Write-Host "Python       : $PythonExe"
Write-Host "Project root : $Root"
Write-Host "Input image  : $imageFile"
Write-Host "Input URI    : $imageUri"
Write-Host "SHA256       : $checksum"
Write-Host "Output prefix: $outputPrefix"
Write-Host "Mode         : $Mode"

Set-TiaEnv

if ($Mode -eq "LocalPipeline") {
    Invoke-LocalPipeline -ImageFile $imageFile -OutputPrefixUri $outputPrefix
    $staging = Join-Path $artifactStaging ($MissionId -replace "/", "_")
    if (Test-Path -LiteralPath $staging) {
        Write-Host ""
        Write-Host "Annotated images:" -ForegroundColor Green
        Get-ChildItem -LiteralPath $staging | ForEach-Object { Write-Host "  $($_.FullName)" }
    }
    exit 0
}

$payload = @{
    workflow_id = $MissionId
    work_item   = $workItem
    command     = "process_intelligence"
    output_hint = "intelligence_packet"
    attachments = @(
        @{
            id        = "att-local-001"
            uri       = $imageUri
            kind      = "image"
            mime_type = $mimeType
            checksum  = @{
                algorithm = "sha256"
                value     = $checksum
            }
            meta = @{
                sensor_id    = "EO-1"
                modality     = "eo_ir"
                platform_lat = $PlatformLat
                platform_lon = $PlatformLon
                altitude_m   = $AltitudeM
                heading_deg  = 0.0
                fov_deg      = 45.0
            }
        }
    )
    input = @{
        recon_report = "Local URI demo without MinIO."
        sector       = "Sector_A"
    }
    context = @{
        jamming_level         = 0.1
        subscriber_agents     = @("commander", "artillery")
        output_storage_prefix = $outputPrefix
        ground_elevation_m    = 120.0
        sensor_telemetry      = @{
            platform_lat = $PlatformLat
            platform_lon = $PlatformLon
            altitude_m   = $AltitudeM
        }
    }
}

$baseUrl = "http://${TiaHost}:${Port}"

try {
    $ready = Invoke-RestMethod -Uri "$baseUrl/ready" -Method Get -TimeoutSec 5
    if (-not $ready.ready) {
        Write-Host "[WARN] Agent ready=false; sendMessage may be rejected" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[ERROR] Cannot reach TIA at $baseUrl" -ForegroundColor Red
    Write-Host "Start agent in another terminal, then rerun this script." -ForegroundColor Yellow
    Write-Host "Or use: .\scripts\demo_local_uri_sendmessage.ps1 -Mode LocalPipeline" -ForegroundColor Yellow
    exit 1
}

$response = Invoke-SendMessage -Payload $payload -BaseUrl $baseUrl -Token $BearerToken
Show-ResultSummary -Response $response -ArtifactStagingRoot $artifactStaging

$packetOutputRoot = Join-Path $Root "data\output\processed"
$intelPacket = $response.output.intelligence_packet
Save-IntelligencePacket -Packet $intelPacket -MissionId $MissionId -OutputRoot $packetOutputRoot

$payloadPath = Join-Path $Root "data\output\demo_local_uri_payload.json"
New-Item -ItemType Directory -Force -Path (Split-Path $payloadPath) | Out-Null
$payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $payloadPath -Encoding UTF8
Write-Host ""
Write-Host "Saved request JSON: $payloadPath" -ForegroundColor DarkGray
