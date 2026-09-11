[CmdletBinding()]
param(
    [string]$PythonPath = $env:ALGOLIB_E2E_PYTHON
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$serviceMain = Join-Path $repoRoot 'services\trajectory_linear_predictor\app\main.py'

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $projectPython = 'D:\software\Miniconda3\envs\algorithm_repo\python.exe'
    $PythonPath = if (Test-Path -LiteralPath $projectPython) {
        $projectPython
    }
    else {
        (Get-Command python -ErrorAction Stop).Source
    }
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python executable not found: $PythonPath"
}
if (-not (Test-Path -LiteralPath $serviceMain)) {
    throw "Python HTTP service not found: $serviceMain"
}

$env:PORT = '9011'
$env:TIA_USE_MOCK = '0'
& $PythonPath $serviceMain
exit $LASTEXITCODE
