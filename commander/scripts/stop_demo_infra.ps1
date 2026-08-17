$patterns = @(
    "nacos-server.jar",
    "mock_auth_server.py"
)

foreach ($pattern in $patterns) {
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -like "*$pattern*" } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force
            Write-Host "[STOP] pid=$($_.ProcessId) pattern=$pattern"
        }
}
