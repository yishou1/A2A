function Initialize-A2ADocker {
    # Desktop's installer may not update an already-open terminal's PATH.
    $command = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue
    if ($command) { return }
    $candidates = @($env:A2A_DOCKER_PATH)
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe')
    }
    foreach ($base in @($env:ProgramFiles, $env:LOCALAPPDATA)) {
        if ($base) { $candidates += (Join-Path $base 'Docker\Docker\resources\bin\docker.exe') }
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $env:PATH = (Split-Path -Parent $candidate) + [IO.Path]::PathSeparator + $env:PATH
            return
        }
    }
    throw 'Docker CLI was not found. Install Docker Desktop, select Linux containers, and allocate at least 12 GB RAM. No host Python or Conda environment is required.'
}

function Start-A2ADockerDesktop {
    # Older Desktop versions do not provide `docker desktop start`.
    $candidates = @()
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe')
    }
    foreach ($base in @($env:ProgramFiles, $env:LOCALAPPDATA)) {
        if ($base) { $candidates += (Join-Path $base 'Docker\Docker\Docker Desktop.exe') }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            Start-Process -FilePath $candidate -WindowStyle Hidden | Out-Null
            return
        }
    }
    & docker desktop start --timeout 180
    if ($LASTEXITCODE -ne 0) { throw 'Docker daemon is unavailable. Start Docker Desktop manually and select Linux containers.' }
}

function Assert-A2ADeploymentFiles {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $required = @(
        'compose.yaml', 'scripts/docker/Dockerfile', 'scripts/docker/Dockerfile.qwen-init',
        'scripts/docker/healthcheck.py', 'scripts/start.sh',
        'algorithmrepo/web/package-lock.json', 'commander/CMakeLists.txt',
        'algorithmrepo/models/checkpoints/edl_head_s.safetensors',
        'algorithmrepo/models/checkpoints/mamba_fusion_s.safetensors',
        'algorithmrepo/models/checkpoints/marl_ppo_scheduler_s.safetensors',
        'algorithmrepo/models/checkpoints/supcon_meta_s.safetensors',
        'algorithmrepo/models/track_threat/st_gnn_ship_kaggle_v1/model.ts',
        'algorithmrepo/knowledge_bases/newport_roe_handbook_2022/indexes/newport-roe-handbook-2022-v1/qwen3-embedding_0.6b/index_manifest.json'
    )
    $indexRoot = 'algorithmrepo/knowledge_bases/newport_roe_handbook_2022/indexes/newport-roe-handbook-2022-v1/qwen3-embedding_0.6b'
    foreach ($indexFile in @('graph.pickle', 'chunk_embeddings/vdb_chunk.parquet',
        'entity_embeddings/vdb_entity.parquet', 'fact_embeddings/vdb_fact.parquet')) {
        $required += "$indexRoot/$indexFile"
    }
    foreach ($relativePath in $required) {
        $path = Join-Path $ProjectRoot $relativePath
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Incomplete deployment: missing $relativePath. Copy the complete repository, including checked-in models and knowledge indexes."
        }
        $stream = [IO.File]::OpenRead($path)
        try {
            if ($stream.Length -eq 0) { throw "Deployment file is empty: $relativePath" }
            $header = New-Object byte[] 128
            $count = $stream.Read($header, 0, $header.Length)
            if ([Text.Encoding]::ASCII.GetString($header, 0, $count).StartsWith('version https://git-lfs.github.com/spec/')) {
                throw "Git LFS pointer found instead of file content: $relativePath. Run git lfs pull before starting."
            }
        }
        finally { $stream.Dispose() }
    }
}

function Test-A2AHostPortInUse {
    param([Parameter(Mandatory = $true)][int]$Port)
    # A short exclusive bind also works where process/socket enumeration is restricted.
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $Port)
    try {
        $listener.ExclusiveAddressUse = $true
        $listener.Start()
        return $false
    }
    catch [Net.Sockets.SocketException] {
        if ($_.Exception.SocketErrorCode -in @([Net.Sockets.SocketError]::AddressAlreadyInUse, [Net.Sockets.SocketError]::AccessDenied)) {
            return $true
        }
        throw
    }
    finally { $listener.Stop() }
}

function Assert-A2AHostPorts {
    param([Parameter(Mandatory = $true)]$Config, [Parameter(Mandatory = $true)][string[]]$ComposePrefix)
    foreach ($service in @('amos', 'a2a-core')) {
        foreach ($binding in $Config.services.$service.ports) {
            $port = [int]$binding.published
            if (-not (Test-A2AHostPortInUse -Port $port)) { continue }
            # A repeat launch may reuse a port owned by this Compose project.
            $endpoint = ''
            try {
                $endpoint = (& docker @ComposePrefix port $service "$($binding.target)" 2>$null | Out-String).Trim()
                if ($LASTEXITCODE -ne 0) { $endpoint = '' }
            }
            catch { $endpoint = '' }
            if ($endpoint -match ":$port$") { continue }
            $variable = if ($service -eq 'amos') { 'AMOS_HOST_PORT' } else { 'A2A_GATEWAY_HOST_PORT' }
            throw "Host port $port is in use by another process. Stop the native/other deployment first, or set $variable to a free port. No running process was stopped."
        }
    }
}

function Get-A2AComposeProjectName {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $normalized = [System.IO.Path]::GetFullPath($ProjectRoot).ToUpperInvariant()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($normalized)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha256.ComputeHash($bytes)
    }
    finally {
        $sha256.Dispose()
    }
    $suffix = [System.BitConverter]::ToString($digest).Replace('-', '').Substring(0, 12).ToLowerInvariant()
    return "a2a613-$suffix"
}

function New-A2AComposeBuildContext {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $fullRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
    if ($fullRoot -notmatch '[^\x00-\x7F]') {
        return [pscustomobject]@{ Path = $fullRoot; Alias = $null; Target = $null }
    }

    # BuildKit's Windows gRPC session headers cannot carry a non-ASCII source
    # path. A directory junction keeps the source in place while presenting an
    # ASCII-only context path to Compose/BuildKit. Keep the Compose project name
    # derived from the real path so its volumes remain stable between runs.
    $candidates = @($env:TEMP, $env:TMP)
    if ($env:WINDIR) {
        $candidates += (Join-Path $env:WINDIR 'Temp')
    }
    $lastError = $null
    foreach ($candidate in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        try {
            $tempRoot = [System.IO.Path]::GetFullPath($candidate)
            if ($tempRoot -match '[^\x00-\x7F]' -or -not (Test-Path -LiteralPath $tempRoot -PathType Container)) {
                continue
            }
            $safeName = Get-A2AComposeProjectName -ProjectRoot $fullRoot
            $aliasPath = Join-Path $tempRoot ("$safeName-build-$([guid]::NewGuid().ToString('N').Substring(0, 8))")
            New-Item -ItemType Junction -Path $aliasPath -Target $fullRoot -ErrorAction Stop | Out-Null
            return [pscustomobject]@{ Path = $aliasPath; Alias = $aliasPath; Target = $fullRoot }
        }
        catch {
            $lastError = $_
        }
    }
    $detail = if ($lastError) { " $($lastError.Exception.Message)" } else { '' }
    throw "Docker BuildKit cannot build from a non-ASCII path and no writable ASCII temporary directory was available.$detail"
}

function Remove-A2AComposeBuildContext {
    param([Parameter(Mandatory = $true)]$Context)

    if (-not $Context.Alias) {
        return
    }
    $aliasPath = [System.IO.Path]::GetFullPath([string]$Context.Alias)
    $item = Get-Item -LiteralPath $aliasPath -Force -ErrorAction Stop
    if ($item.LinkType -ne 'Junction') {
        throw "Refusing to remove non-junction BuildKit alias: $aliasPath"
    }
    $target = [System.IO.Path]::GetFullPath([string]$item.Target)
    if (-not [string]::Equals($target, [string]$Context.Target, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove BuildKit alias with unexpected target: $aliasPath"
    }
    # Directory.Delete(path, false) removes the junction itself on Windows;
    # Remove-Item has a PowerShell 7 NullReferenceException for this case.
    [System.IO.Directory]::Delete($aliasPath, $false)
}
