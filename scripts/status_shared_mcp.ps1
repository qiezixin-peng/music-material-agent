$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stateFile = Join-Path $projectRoot "data\runtime\shared-mcp.json"
$port = 8766
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
$state = if (Test-Path -LiteralPath $stateFile -PathType Leaf) {
    Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
} else {
    $null
}

[pscustomobject]@{
    status = if ($listener) { "RUNNING" } else { "STOPPED" }
    endpoint = "http://127.0.0.1:$port/mcp"
    listening_pid = if ($listener) { $listener[0].OwningProcess } else { $null }
    recorded_server_pid = if ($state) { $state.server_pid } else { $null }
    launcher_pid = if ($state) { $state.launcher_pid } else { $null }
    started_at = if ($state) { $state.started_at } else { $null }
    stderr_log = if ($state) { $state.stderr_log } else { $null }
} | ConvertTo-Json
