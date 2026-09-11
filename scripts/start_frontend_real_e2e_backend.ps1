[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 18088
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $repoRoot 'web\test-results\real-e2e'
$registryPath = Join-Path $runtimeRoot 'registry.json'
$executionLogPath = Join-Path $runtimeRoot 'execution.jsonl'
$algolibPath = Join-Path $repoRoot 'build-smoke-offline\Debug\algolib.exe'
$serverPath = Join-Path $repoRoot 'build-smoke-offline\Debug\algolib_server.exe'
$onnxPackagePath = Join-Path $repoRoot 'examples\compliance_risk_scorer_onnx\1.0.0'
$pythonPackagePath = Join-Path $repoRoot 'examples\trajectory_linear_predictor\1.0.0'
$functionCatalogPath = Join-Path $repoRoot 'config\operational_function_catalog.yaml'

foreach ($requiredPath in @($algolibPath, $serverPath, $onnxPackagePath, $pythonPackagePath, $functionCatalogPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Real E2E prerequisite not found: $requiredPath"
    }
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
foreach ($stateFile in @($registryPath, $executionLogPath)) {
    if (Test-Path -LiteralPath $stateFile) {
        Remove-Item -LiteralPath $stateFile -Force
    }
}

$previousRegistryPath = $env:ALGOLIB_REGISTRY_PATH
$env:ALGOLIB_REGISTRY_PATH = $registryPath

try {
    & $algolibPath register $onnxPackagePath
    if ($LASTEXITCODE -ne 0) { throw 'Failed to register the real ONNX E2E algorithm.' }

    & $algolibPath activate compliance_risk_scorer_onnx 1.0.0 onnx
    if ($LASTEXITCODE -ne 0) { throw 'Failed to activate the real ONNX E2E algorithm.' }

    & $algolibPath register $pythonPackagePath
    if ($LASTEXITCODE -ne 0) { throw 'Failed to register the real Python HTTP E2E algorithm.' }

    & $algolibPath activate trajectory_linear_predictor 1.0.0 python_http_service
    if ($LASTEXITCODE -ne 0) { throw 'Failed to activate the real Python HTTP E2E algorithm.' }

    & $serverPath `
        --host 127.0.0.1 `
        --port $Port `
        --registry $registryPath `
        --execution-log $executionLogPath `
        --function-catalog $functionCatalogPath

    exit $LASTEXITCODE
}
finally {
    $env:ALGOLIB_REGISTRY_PATH = $previousRegistryPath
}
