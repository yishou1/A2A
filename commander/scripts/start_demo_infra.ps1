$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$NacosHome = "D:\tools\nacos"
$PythonExe = "D:\tools\miniforge3\envs\a2a\python.exe"
$LogDir = Join-Path $ProjectRoot ".a2a_state\infra_logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Test-HttpReady($Url, $TimeoutSec = 2) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        return $response.StatusCode -lt 500
    } catch {
        return $false
    }
}

if (!(Test-Path (Join-Path $NacosHome "bin\startup.cmd"))) {
    throw "Nacos not found at $NacosHome. Expected bin\startup.cmd."
}

if (!(Test-Path $PythonExe)) {
    throw "Python env not found at $PythonExe."
}

if (Test-HttpReady "http://127.0.0.1:8848/nacos/v1/console/health/readiness") {
    Write-Host "[OK] Nacos already ready at 127.0.0.1:8848"
} else {
    Write-Host "[START] Nacos standalone at 127.0.0.1:8848"
    Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList "/c", "startup.cmd -m standalone" `
        -WorkingDirectory (Join-Path $NacosHome "bin") `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "nacos.out.log") `
        -RedirectStandardError (Join-Path $LogDir "nacos.err.log")
}

if (Test-HttpReady "http://127.0.0.1:8080/get") {
    Write-Host "[OK] Auth mock already ready at 127.0.0.1:8080"
} else {
    Write-Host "[START] Auth mock at 127.0.0.1:8080"
    Start-Process `
        -FilePath $PythonExe `
        -ArgumentList "scripts/mock_auth_server.py", "--host", "127.0.0.1", "--port", "8080" `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "auth.out.log") `
        -RedirectStandardError (Join-Path $LogDir "auth.err.log")
}

$deadline = (Get-Date).AddSeconds(90)
do {
    $nacosReady = Test-HttpReady "http://127.0.0.1:8848/nacos/v1/console/health/readiness" 3
    $authReady = Test-HttpReady "http://127.0.0.1:8080/get" 2
    if ($nacosReady -and $authReady) {
        Write-Host "[READY] Nacos and auth mock are ready."
        exit 0
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

throw "Demo infrastructure did not become ready. Check logs in $LogDir."
