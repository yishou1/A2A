param(
    [string]$Python = "python"
)

$Root = Split-Path -Parent $PSScriptRoot
$Services = Join-Path $Root "services"

$Algorithms = @(
    @{ Id = "execution_rule_matcher"; Port = 9010 },
    @{ Id = "trajectory_linear_predictor"; Port = 9011 },
    @{ Id = "execution_control_planner"; Port = 9012 },
    @{ Id = "mission_feature_adapter"; Port = 9013 },
    @{ Id = "mission_completion_scorer"; Port = 9014 },
    @{ Id = "closed_loop_decision_advisor"; Port = 9015 },
    @{ Id = "xbd_damage_assessor"; Port = 9016 },
    @{ Id = "decision_planning_core"; Port = 9020 },
    @{ Id = "compliance_authorization_core"; Port = 9021 }
)

Write-Host "Installing Python service dependencies..."
& $Python -m pip install -r (Join-Path $Services "requirements.txt")

foreach ($item in $Algorithms) {
    $env:PORT = "$($item.Port)"
    $main = Join-Path $Services "$($item.Id)\app\main.py"
    Write-Host "Starting $($item.Id) on port $($item.Port)..."
    Start-Process -FilePath $Python -ArgumentList $main -WorkingDirectory $Root -WindowStyle Minimized
}

Write-Host "All A2A algorithm services started."
