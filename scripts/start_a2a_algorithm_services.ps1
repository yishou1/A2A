param(
    [string]$Python = "python",
    [switch]$TiaOnly,
    [switch]$LegacyOnly
)

$Root = Split-Path -Parent $PSScriptRoot
$Services = Join-Path $Root "services"

$LegacyAlgorithms = @(
    @{ Id = "execution_rule_matcher"; Port = 9010 },
    @{ Id = "trajectory_linear_predictor"; Port = 9011 },
    @{ Id = "execution_control_planner"; Port = 9012 },
    @{ Id = "mission_feature_adapter"; Port = 9013 },
    @{ Id = "mission_completion_scorer"; Port = 9014 },
    @{ Id = "closed_loop_decision_advisor"; Port = 9015 },
    @{ Id = "xbd_damage_assessor"; Port = 9016 }
)

$TiaAlgorithms = @(
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
)

if ($TiaOnly) {
    $Algorithms = $TiaAlgorithms
} elseif ($LegacyOnly) {
    $Algorithms = $LegacyAlgorithms
} else {
    $Algorithms = $LegacyAlgorithms + $TiaAlgorithms
}

Write-Host "Installing Python service dependencies..."
& $Python -m pip install -r (Join-Path $Services "requirements.txt")

$env:TIA_USE_MOCK = "1"

foreach ($item in $Algorithms) {
    $env:PORT = "$($item.Port)"
    $main = Join-Path $Services "$($item.Id)\app\main.py"
    Write-Host "Starting $($item.Id) on port $($item.Port)..."
    Start-Process -FilePath $Python -ArgumentList $main -WorkingDirectory $Root -WindowStyle Minimized
}

Write-Host "Started $($Algorithms.Count) algorithm services."
