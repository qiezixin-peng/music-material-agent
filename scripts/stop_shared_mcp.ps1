$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stateFile = Join-Path $projectRoot "data\runtime\shared-mcp.json"
if (-not (Test-Path -LiteralPath $stateFile -PathType Leaf)) {
    [pscustomobject]@{ status = "STOPPED"; reason = "NO_STATE_FILE" } | ConvertTo-Json
    exit 0
}

$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
$server = Get-Process -Id ([int]$state.server_pid) -ErrorAction SilentlyContinue
$launcher = Get-Process -Id ([int]$state.launcher_pid) -ErrorAction SilentlyContinue
if (-not $server -and -not $launcher) {
    [pscustomobject]@{ status = "STOPPED"; reason = "PROCESS_NOT_FOUND" } | ConvertTo-Json
    exit 0
}

if ($server) {
    $serverInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$($server.Id)"
    if (-not $serverInfo.CommandLine -or $serverInfo.CommandLine -notmatch "music_material_agent\.cli.+mcp-http") {
        throw "Refusing to stop PID $($server.Id): command line does not match the shared MCP"
    }
    Stop-Process -Id $server.Id
    $server.WaitForExit(10000) | Out-Null
}
if ($launcher) {
    $launcherInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$($launcher.Id)"
    if ($launcherInfo.CommandLine -and $launcherInfo.CommandLine -match "music_material_agent\.cli.+mcp-http") {
        Stop-Process -Id $launcher.Id -ErrorAction SilentlyContinue
    }
}
[pscustomobject]@{ status = "STOPPED"; server_pid = $state.server_pid } | ConvertTo-Json
