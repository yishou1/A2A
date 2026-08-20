param(
    [string]$Model = "qwen2.5:3b"
)

Write-Host "=== TIA 小模型从零检查 ===" -ForegroundColor Cyan
Write-Host "目标模型: $Model"

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host "[X] 未找到 ollama 命令。" -ForegroundColor Red
    Write-Host "    请先安装: https://ollama.com/download"
    Write-Host "    或: winget install Ollama.Ollama"
    exit 1
}
Write-Host "[OK] ollama: $($ollama.Source)"

try {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    Write-Host "[OK] Ollama 服务在线 (11434)"
    $names = @($tags.models | ForEach-Object { $_.name })
    Write-Host "    已有模型: $($names -join ', ')"
    if ($names -notcontains $Model -and -not ($names | Where-Object { $_ -like "$Model*" })) {
        Write-Host "[!] 未找到 $Model ，正在拉取..." -ForegroundColor Yellow
        ollama pull $Model
    }
} catch {
    Write-Host "[X] 连不上 http://127.0.0.1:11434" -ForegroundColor Red
    Write-Host "    请先启动: ollama serve"
    Write-Host "    然后再执行: ollama pull $Model"
    exit 2
}

Write-Host ""
Write-Host "请在当前终端设置并验证:" -ForegroundColor Green
Write-Host @"
`$env:ENABLE_LLM="true"
`$env:TIA_ALGORITHM_PLANNER="llm"
`$env:TOOL_LLM_URL="http://127.0.0.1:11434/v1"
`$env:TOOL_LLM_NAME="$Model"
`$env:API_KEY="ollama"
`$env:LLM_STRIP_THINKING="true"
`$env:LLM_JSON_RETRY_COUNT="1"
.\.venv\Scripts\python.exe scripts\verify_tia_llm_lzh_ollama.py
"@
