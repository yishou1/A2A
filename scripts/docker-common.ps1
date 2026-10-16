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
