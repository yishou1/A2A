param(
  [ValidateSet("offline", "azure", "local-qwen-gpu")]
  [string]$Profile = "offline",
  [switch]$RequireLlm,
  [switch]$Status,
  [switch]$Stop
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Repo

if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
  throw "WSL is not installed. Install it with: wsl --install Ubuntu"
}

$wslList = (wsl -l -q 2>$null) -join ""
if ([string]::IsNullOrWhiteSpace($wslList)) {
  throw "No WSL Linux distribution is installed. Run: wsl --install Ubuntu"
}

$repoForWsl = (wsl wslpath -a "$Repo").Trim()

if ($Status) {
  wsl bash -lc "cd '$repoForWsl' && ./scripts/status.sh"
  exit $LASTEXITCODE
}

if ($Stop) {
  wsl bash -lc "cd '$repoForWsl' && ./scripts/stop.sh"
  exit $LASTEXITCODE
}

$argsForStart = @()
if ($Profile -eq "offline") {
  $argsForStart += "--offline"
} else {
  $argsForStart += "--llm-profile $Profile"
  if ($RequireLlm) {
    $argsForStart += "--require-llm"
  }
}

wsl bash -lc "cd '$repoForWsl' && ./scripts/start.sh $($argsForStart -join ' ')"
