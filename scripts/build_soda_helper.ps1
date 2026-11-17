$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$source = Join-Path $projectRoot 'helpers\soda-agent-helper'
$outputDirectory = Join-Path $projectRoot 'bin'
$output = Join-Path $outputDirectory 'soda-agent-helper.exe'
$go = (Get-Command go -ErrorAction SilentlyContinue).Source
if (-not $go -and (Test-Path 'C:\Program Files\Go\bin\go.exe')) {
    $go = 'C:\Program Files\Go\bin\go.exe'
}
if (-not $go) {
    throw 'Go toolchain is required to build soda-agent-helper.'
}

New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$env:CGO_ENABLED = '0'
Push-Location $source
try {
    & $go mod download
    if ($LASTEXITCODE -ne 0) {
        throw "soda-agent-helper dependency download failed with exit code $LASTEXITCODE"
    }
    & $go build -trimpath -ldflags '-s -w' -o $output .
    if ($LASTEXITCODE -ne 0) {
        throw "soda-agent-helper build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $output).Hash.ToLowerInvariant()
$version = (& $go version).Trim()
$manifest = @{
    artifact = 'bin/soda-agent-helper.exe'
    sha256 = $hash
    go = $version
    module = 'music-material-agent/soda-agent-helper'
    music_lib_commit = '044c84bed714058863f04ca5e0128d18f02ebba5'
    music_lib_version = 'v1.1.3-0.20261116090354-044c84bed714'
    interface = 'stdin/stdout JSON; no Web UI or listening port'
}
$manifestPath = Join-Path $outputDirectory 'soda-agent-helper.build.json'
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding utf8
Write-Output (@{ path = $output; sha256 = $hash } | ConvertTo-Json -Compress)
