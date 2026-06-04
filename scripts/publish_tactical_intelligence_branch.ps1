# 将战术情报 Agent 增量推送到 https://github.com/yishou1/A2A
# 用法（在项目根目录 PowerShell）:
#   .\scripts\publish_tactical_intelligence_branch.ps1
# 可选: -BranchName "feat/tactical-intelligence-agent"

param(
    [string]$BranchName = "feat/tactical-intelligence-agent",
    [string]$RemoteUrl = "https://github.com/yishou1/A2A.git"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "==> 工作目录: $Root"
Write-Host "==> 目标分支: $BranchName"

if (-not (Test-Path ".git")) {
    git init
    git remote add origin $RemoteUrl
}

git fetch origin main
if ($LASTEXITCODE -ne 0) {
    Write-Error "无法 fetch origin/main，请检查网络与 GitHub 登录（git credential / SSH）。"
}

$hasMain = git rev-parse --verify origin/main 2>$null
if (-not $hasMain) {
    Write-Error "未找到 origin/main"
}

git checkout -B $BranchName origin/main

# 增量目录/文件（相对 upstream main 新增或修改的部分）
$paths = @(
    "agent",
    "tactical_intelligence_agent",
    "config",
    ".env.example",
    "docker-compose.yml",
    ".gitignore",
    "scripts/build_situation.py",
    "scripts/demo_tactical_intelligence_acceptance.py",
    "scripts/download_models.py",
    "scripts/run_simulation.py",
    "scripts/verify_commander_a2a.py",
    "scripts/simulation"
)

foreach ($p in $paths) {
    if (Test-Path $p) {
        git add $p
    }
}

git status --short
$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Warning "没有可提交的变更；若已在 feat 分支提交过，可直接: git push -u origin $BranchName"
    exit 0
}

git -c user.name="yishou1" -c user.email="212996678+users.noreply.github.com" commit -m @"
Add tactical intelligence agent and multimodal inference pipeline.

Integrate tactical_intelligence_agent with A2A Commander protocol, agent/
three-skill pipeline, iron valley simulation scripts, and demo acceptance flow.
"@

git push -u origin $BranchName
Write-Host ""
Write-Host "完成。在浏览器打开:"
Write-Host "  https://github.com/yishou1/A2A/compare/main...$($BranchName -replace '/','%2F')?expand=1"
