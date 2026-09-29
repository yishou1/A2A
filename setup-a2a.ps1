param(
  [switch]$GpuTorch
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Repo

function Test-Command($Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host "[repo] $Repo"
$branch = git branch --show-current
Write-Host "[git] branch=$branch"
if ($branch -ne "jzz/integrated") {
  throw "Current branch is '$branch'. Run: git switch jzz/integrated"
}

if (-not (Test-Command "wsl")) {
  throw "WSL is not installed. Install it with: wsl --install Ubuntu"
}

$wslList = (wsl -l -q 2>$null) -join ""
if ([string]::IsNullOrWhiteSpace($wslList)) {
  Write-Host "[missing] No WSL Linux distribution is installed."
  Write-Host "Install Ubuntu first:"
  Write-Host "  wsl --install Ubuntu"
  Write-Host "Then reopen PowerShell and run:"
  Write-Host "  .\setup-a2a.ps1"
  exit 1
}

$repoForWsl = (wsl wslpath -a ($Repo -replace '\\','/')).Trim()
$torchPrefix = ""
if ($GpuTorch) {
  $torchPrefix = "A2A_TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121 "
}

Write-Host "[setup] running scripts/bootstrap.sh inside WSL"
wsl bash -lc "cd '$repoForWsl' && ${torchPrefix}./scripts/bootstrap.sh"
