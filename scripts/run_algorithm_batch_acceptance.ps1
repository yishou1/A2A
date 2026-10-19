[CmdletBinding()]
param(
    [ValidateSet("core-basic", "m01-clustering", "m02-association", "m03-linear-regression", "m04-logistic-regression", "m05-random-forest", "m06-neural-network", "m07-naive-bayes", "m08-gan", "m12-fedavg", "m13-reinforcement-learning", "m14-explainable-ai", "m15-multimodal-fusion", "m16-time-series", "m20-graph-neural-network", "track-threat", "tia-mock", "tia-real", "auxiliary", "external-a2a", "onnx-stub", "onnx-all", "static-all")]
    [string]$Group = "core-basic",
    [string]$Python = "python",
    [string]$Algolib = "",
    [string]$ReportRoot = "",
    [ValidateRange(1, 300)]
    [int]$TimeoutSeconds = 30,
    [switch]$RealGate,
    [switch]$KeepServices
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
if (-not $Algolib) {
    $Algolib = Join-Path $Root "build-smoke-offline\Debug\algolib.exe"
}
if (-not $ReportRoot) {
    $ReportRoot = Join-Path $Root "build-smoke-offline\acceptance-batches"
}

$Algolib = [System.IO.Path]::GetFullPath($Algolib)
$ReportRoot = [System.IO.Path]::GetFullPath($ReportRoot)
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ReportDir = Join-Path $ReportRoot "$Group-$stamp"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

$CoreBasicAlgorithms = @(
    "execution_rule_matcher",
    "trajectory_linear_predictor",
    "execution_control_planner",
    "mission_feature_adapter",
    "mission_completion_scorer",
    "closed_loop_decision_advisor"
)

$M01Algorithms = @(
    "clustering_engine"
)

$M02Algorithms = @(
    "execution_rule_matcher"
)

$M03Algorithms = @(
    "trajectory_linear_predictor"
)

$M04Algorithms = @(
    "decision_plan_recommender_onnx",
    "compliance_risk_scorer_onnx"
)

$M05Algorithms = @(
    "threat_priority_random_forest"
)

$M06Algorithms = @(
    "supcon_meta_classifier"
)

$M07Algorithms = @(
    "intent_gaussian_naive_bayes"
)

$M08Algorithms = @(
    "conditional_tabular_gan"
)

$M12Algorithms = @(
    "federated_fedavg_aggregator"
)

$M13Algorithms = @(
    "marl_ppo_task_scheduler"
)

$M14Algorithms = @(
    "edl_evidential_verifier"
)

$M15Algorithms = @(
    "multimodal_mamba_fusion"
)

$M16Algorithms = @(
    "target_trend_predictor_onnx"
)

$M20Algorithms = @(
    "graph_relation_reasoner"
)

$TrackThreatAlgorithms = @(
    "multimodal_feature_fuser",
    "target_type_classifier",
    "track_state_updater",
    "trajectory_predictor",
    "graph_relation_reasoner"
)

$TiaAlgorithms = @(
    "battlefield_rtdetr_detector",
    "siamese_mask2former_damage",
    "edl_evidential_verifier",
    "motr_neural_kalman_tracker",
    "marl_ppo_task_scheduler",
    "imagebind_multimodal_encoder",
    "multimodal_mamba_fusion",
    "supcon_meta_classifier",
    "synapse_rag_retriever",
    "knowledge_semantic_comm",
    "marl_dynamic_router"
)

$AuxiliaryAlgorithms = @(
    "xbd_damage_assessor"
)

$ExternalA2aAlgorithms = @(
    "decision_planning_core",
    "compliance_authorization_core"
)

function New-ServiceDefinition {
    param(
        [string]$Name,
        [int]$Port,
        [string]$Main,
        [string]$HealthUrl,
        [string]$ExpectedAlgorithmId = ""
    )
    [PSCustomObject]@{
        Name = $Name
        Port = $Port
        Main = $Main
        HealthUrl = $HealthUrl
        ExpectedAlgorithmId = $ExpectedAlgorithmId
    }
}

$CoreServices = foreach ($item in @(
    @{ Id = "execution_rule_matcher"; Port = 9010 },
    @{ Id = "trajectory_linear_predictor"; Port = 9011 },
    @{ Id = "execution_control_planner"; Port = 9012 },
    @{ Id = "mission_feature_adapter"; Port = 9013 },
    @{ Id = "mission_completion_scorer"; Port = 9014 },
    @{ Id = "closed_loop_decision_advisor"; Port = 9015 }
)) {
    New-ServiceDefinition `
        -Name $item.Id `
        -Port $item.Port `
        -Main "services\$($item.Id)\app\main.py" `
        -HealthUrl "http://127.0.0.1:$($item.Port)/health" `
        -ExpectedAlgorithmId $item.Id
}

$M01Services = @(
    New-ServiceDefinition `
        -Name "clustering_engine" `
        -Port 9031 `
        -Main "services\clustering_engine\app\main.py" `
        -HealthUrl "http://127.0.0.1:9031/health" `
        -ExpectedAlgorithmId "clustering_engine"
)

$M02Services = @(
    New-ServiceDefinition `
        -Name "execution_rule_matcher" `
        -Port 9010 `
        -Main "services\execution_rule_matcher\app\main.py" `
        -HealthUrl "http://127.0.0.1:9010/health" `
        -ExpectedAlgorithmId "execution_rule_matcher"
)

$M03Services = @(
    New-ServiceDefinition `
        -Name "trajectory_linear_predictor" `
        -Port 9011 `
        -Main "services\trajectory_linear_predictor\app\main.py" `
        -HealthUrl "http://127.0.0.1:9011/health" `
        -ExpectedAlgorithmId "trajectory_linear_predictor"
)

$M05Services = @(
    New-ServiceDefinition `
        -Name "threat_priority_random_forest" `
        -Port 9032 `
        -Main "services\threat_priority_random_forest\app\main.py" `
        -HealthUrl "http://127.0.0.1:9032/health" `
        -ExpectedAlgorithmId "threat_priority_random_forest"
)

$M06Services = @(
    New-ServiceDefinition `
        -Name "supcon_meta_classifier" `
        -Port 9027 `
        -Main "services\supcon_meta_classifier\app\main.py" `
        -HealthUrl "http://127.0.0.1:9027/health" `
        -ExpectedAlgorithmId "supcon_meta_classifier"
)

$M07Services = @(
    New-ServiceDefinition `
        -Name "intent_gaussian_naive_bayes" `
        -Port 9033 `
        -Main "services\intent_gaussian_naive_bayes\app\main.py" `
        -HealthUrl "http://127.0.0.1:9033/health" `
        -ExpectedAlgorithmId "intent_gaussian_naive_bayes"
)

$M08Services = @(
    New-ServiceDefinition `
        -Name "conditional_tabular_gan" `
        -Port 9035 `
        -Main "services\conditional_tabular_gan\app\main.py" `
        -HealthUrl "http://127.0.0.1:9035/health" `
        -ExpectedAlgorithmId "conditional_tabular_gan"
)

$M12Services = @(
    New-ServiceDefinition `
        -Name "federated_fedavg_aggregator" `
        -Port 9034 `
        -Main "services\federated_fedavg_aggregator\app\main.py" `
        -HealthUrl "http://127.0.0.1:9034/health" `
        -ExpectedAlgorithmId "federated_fedavg_aggregator"
)

$M13Services = @(
    New-ServiceDefinition `
        -Name "marl_ppo_task_scheduler" `
        -Port 9024 `
        -Main "services\marl_ppo_task_scheduler\app\main.py" `
        -HealthUrl "http://127.0.0.1:9024/health" `
        -ExpectedAlgorithmId "marl_ppo_task_scheduler"
)

$M14Services = @(
    New-ServiceDefinition `
        -Name "edl_evidential_verifier" `
        -Port 9022 `
        -Main "services\edl_evidential_verifier\app\main.py" `
        -HealthUrl "http://127.0.0.1:9022/health" `
        -ExpectedAlgorithmId "edl_evidential_verifier"
)

$M15Services = @(
    New-ServiceDefinition `
        -Name "multimodal_mamba_fusion" `
        -Port 9026 `
        -Main "services\multimodal_mamba_fusion\app\main.py" `
        -HealthUrl "http://127.0.0.1:9026/health" `
        -ExpectedAlgorithmId "multimodal_mamba_fusion"
)

$M20Services = @(
    New-ServiceDefinition `
        -Name "graph_relation_reasoner" `
        -Port 9022 `
        -Main "services\track_threat_algorithms\app\main.py" `
        -HealthUrl "http://127.0.0.1:9022/graph_relation_reasoner/health" `
        -ExpectedAlgorithmId "graph_relation_reasoner"
)

$TrackServices = @(
    New-ServiceDefinition `
        -Name "track_threat_algorithms" `
        -Port 9022 `
        -Main "services\track_threat_algorithms\app\main.py" `
        -HealthUrl "http://127.0.0.1:9022/health"
)

$TiaServices = foreach ($item in @(
    @{ Id = "battlefield_rtdetr_detector"; Port = 9020 },
    @{ Id = "siamese_mask2former_damage"; Port = 9021 },
    @{ Id = "edl_evidential_verifier"; Port = 9022 },
    @{ Id = "motr_neural_kalman_tracker"; Port = 9023 },
    @{ Id = "marl_ppo_task_scheduler"; Port = 9024 },
    @{ Id = "imagebind_multimodal_encoder"; Port = 9025 },
    @{ Id = "multimodal_mamba_fusion"; Port = 9026 },
    @{ Id = "supcon_meta_classifier"; Port = 9027 },
    @{ Id = "synapse_rag_retriever"; Port = 9028 },
    @{ Id = "knowledge_semantic_comm"; Port = 9029 },
    @{ Id = "marl_dynamic_router"; Port = 9030 }
)) {
    New-ServiceDefinition `
        -Name $item.Id `
        -Port $item.Port `
        -Main "services\$($item.Id)\app\main.py" `
        -HealthUrl "http://127.0.0.1:$($item.Port)/health" `
        -ExpectedAlgorithmId $item.Id
}

$AuxiliaryServices = foreach ($item in @(
    @{ Id = "xbd_damage_assessor"; Port = 9016 }
)) {
    New-ServiceDefinition `
        -Name $item.Id `
        -Port $item.Port `
        -Main "services\$($item.Id)\app\main.py" `
        -HealthUrl "http://127.0.0.1:$($item.Port)/health" `
        -ExpectedAlgorithmId $item.Id
}

$ExternalA2aServices = foreach ($item in @(
    @{ Id = "decision_planning_core"; Port = 9020 },
    @{ Id = "compliance_authorization_core"; Port = 9021 }
)) {
    New-ServiceDefinition `
        -Name $item.Id `
        -Port $item.Port `
        -Main "services\$($item.Id)\app\main.py" `
        -HealthUrl "http://127.0.0.1:$($item.Port)/health" `
        -ExpectedAlgorithmId $item.Id
}

$Algorithms = @()
$Services = @()
$RunRuntime = $false
$RunAlgolib = $false
$ForbidFallback = $false

switch ($Group) {
    "core-basic" {
        $Algorithms = $CoreBasicAlgorithms
        $Services = $CoreServices
        $RunRuntime = $true
        $RunAlgolib = $true
    }
    "m01-clustering" {
        $Algorithms = $M01Algorithms
        $Services = $M01Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m02-association" {
        $Algorithms = $M02Algorithms
        $Services = $M02Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m03-linear-regression" {
        $Algorithms = $M03Algorithms
        $Services = $M03Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m04-logistic-regression" {
        $Algorithms = $M04Algorithms
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m05-random-forest" {
        $Algorithms = $M05Algorithms
        $Services = $M05Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m06-neural-network" {
        $Algorithms = $M06Algorithms
        $Services = $M06Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m07-naive-bayes" {
        $Algorithms = $M07Algorithms
        $Services = $M07Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m08-gan" {
        $Algorithms = $M08Algorithms
        $Services = $M08Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m12-fedavg" {
        $Algorithms = $M12Algorithms
        $Services = $M12Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m13-reinforcement-learning" {
        $Algorithms = $M13Algorithms
        $Services = $M13Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m14-explainable-ai" {
        $Algorithms = $M14Algorithms
        $Services = $M14Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m15-multimodal-fusion" {
        $Algorithms = $M15Algorithms
        $Services = $M15Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m16-time-series" {
        $Algorithms = $M16Algorithms
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "m20-graph-neural-network" {
        $Algorithms = $M20Algorithms
        $Services = $M20Services
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "track-threat" {
        $Algorithms = $TrackThreatAlgorithms
        $Services = $TrackServices
        $RunRuntime = $true
        $RunAlgolib = $true
    }
    "tia-mock" {
        $Algorithms = $TiaAlgorithms
        $Services = $TiaServices
        $RunRuntime = $true
        $RunAlgolib = $true
    }
    "tia-real" {
        $Algorithms = $TiaAlgorithms
        $Services = $TiaServices
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "auxiliary" {
        $Algorithms = $AuxiliaryAlgorithms
        $Services = $AuxiliaryServices
        $RunRuntime = $true
        $RunAlgolib = $true
    }
    "external-a2a" {
        $Algorithms = $ExternalA2aAlgorithms
        $Services = $ExternalA2aServices
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "onnx-stub" {
        $Algorithms = @("onnx_text_classifier")
        $RunAlgolib = $true
    }
    "onnx-all" {
        $Algorithms = @(
            "compliance_risk_scorer_onnx",
            "decision_plan_recommender_onnx",
            "onnx_text_classifier",
            "target_trend_predictor_onnx"
        )
        $RunRuntime = $true
        $RunAlgolib = $true
        $ForbidFallback = $true
    }
    "static-all" {
        # No filters: validate all packages without starting any backend.
    }
}

if ($RealGate) {
    $ForbidFallback = $true
}

function Test-ServiceReady {
    param($Definition)
    try {
        $health = Invoke-RestMethod `
            -Uri $Definition.HealthUrl `
            -Method Get `
            -TimeoutSec 2
        if (-not $health.ok) {
            return $false
        }
        if ($Definition.ExpectedAlgorithmId -and
            $health.algorithm_id -ne $Definition.ExpectedAlgorithmId) {
            return $false
        }
        return $true
    }
    catch {
        return $false
    }
}

function Wait-ServiceReady {
    param($Definition, [System.Diagnostics.Process]$Process)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            return $false
        }
        if (Test-ServiceReady $Definition) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

& $Python -c "import yaml" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyYAML is required. Install it with: $Python -m pip install pyyaml"
}
if ($RunAlgolib -and -not (Test-Path -LiteralPath $Algolib -PathType Leaf)) {
    throw "algolib executable not found: $Algolib"
}
if ($Group -eq "external-a2a") {
    $a2aRoot = if ($env:A2A_REPO_ROOT) {
        [System.IO.Path]::GetFullPath($env:A2A_REPO_ROOT)
    } else {
        Join-Path (Split-Path -Parent $Root) "A2A"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $a2aRoot "decision_agents") -PathType Container)) {
        throw "external-a2a requires the companion A2A repository. Set A2A_REPO_ROOT to a directory containing decision_agents (checked: $a2aRoot)."
    }
}

$StartedProcesses = @()
$PreviousTiaUseMock = $env:TIA_USE_MOCK
try {
    if ($Group -in @("tia-mock", "tia-real", "m06-neural-network", "m13-reinforcement-learning", "m14-explainable-ai", "m15-multimodal-fusion")) {
        $env:TIA_USE_MOCK = if ($Group -eq "tia-mock") { "1" } else { "0" }
    }

    foreach ($service in $Services) {
        if (Test-ServiceReady $service) {
            Write-Host "[REUSE] $($service.Name) at $($service.HealthUrl)"
            continue
        }

        $listener = Get-NetTCPConnection `
            -State Listen `
            -LocalPort $service.Port `
            -ErrorAction SilentlyContinue
        if ($listener) {
            throw "Port $($service.Port) is already occupied, but $($service.Name) health/identity check failed."
        }

        $mainPath = Join-Path $Root $service.Main
        if (-not (Test-Path -LiteralPath $mainPath -PathType Leaf)) {
            throw "Service entrypoint not found: $mainPath"
        }

        $env:PORT = [string]$service.Port
        $stdoutPath = Join-Path $ReportDir "$($service.Name).stdout.log"
        $stderrPath = Join-Path $ReportDir "$($service.Name).stderr.log"
        Write-Host "[START] $($service.Name) on port $($service.Port)"
        $process = Start-Process `
            -FilePath $Python `
            -ArgumentList @("`"$mainPath`"") `
            -WorkingDirectory $Root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -PassThru
        $StartedProcesses += $process

        if (-not (Wait-ServiceReady $service $process)) {
            $healthDetail = try {
                (Invoke-RestMethod `
                    -Uri $service.HealthUrl `
                    -Method Get `
                    -TimeoutSec 2 | ConvertTo-Json -Compress)
            }
            catch {
                "unavailable: $($_.Exception.Message)"
            }
            $detail = if (Test-Path -LiteralPath $stderrPath) {
                (Get-Content -LiteralPath $stderrPath -Tail 20) -join "`n"
            } else {
                "no stderr log"
            }
            throw "$($service.Name) did not become ready. Health: $healthDetail`nLog:`n$detail"
        }
        Write-Host "[READY] $($service.Name)"
    }

    $AcceptScript = Join-Path $Root "scripts\accept_all_algorithms.py"
    $AcceptArgs = @(
        $AcceptScript,
        "--report-dir", $ReportDir,
        "--timeout", [string]$TimeoutSeconds
    )
    if ($RunRuntime) {
        $AcceptArgs += "--runtime"
    }
    if ($RunAlgolib) {
        $AcceptArgs += @("--algolib", $Algolib)
    }
    if ($ForbidFallback) {
        $AcceptArgs += "--forbid-fallback"
    }
    foreach ($algorithm in $Algorithms) {
        $AcceptArgs += @("--algorithm", $algorithm)
    }

    Write-Host "[ACCEPT] group=$Group algorithms=$($Algorithms.Count)"
    & $Python @AcceptArgs
    $AcceptanceExitCode = $LASTEXITCODE
    if ($AcceptanceExitCode -ne 0) {
        throw "Acceptance failed with exit code $AcceptanceExitCode. See $ReportDir"
    }

    Write-Host "[PASS] $Group acceptance completed."
    Write-Host "Report: $(Join-Path $ReportDir 'acceptance_report.md')"
}
finally {
    $env:TIA_USE_MOCK = $PreviousTiaUseMock
    if (-not $KeepServices) {
        foreach ($process in $StartedProcesses) {
            if (-not $process.HasExited) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                Write-Host "[STOP] process $($process.Id)"
            }
        }
    } elseif ($StartedProcesses.Count -gt 0) {
        Write-Host "[KEEP] $($StartedProcesses.Count) service process(es) left running."
    }
}
