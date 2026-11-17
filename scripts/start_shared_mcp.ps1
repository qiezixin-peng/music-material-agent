$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$stateDir = Join-Path $projectRoot "data\runtime"
$stateFile = Join-Path $stateDir "shared-mcp.json"
$logDir = "D:\三喜\临时文件\音乐物料MCP服务-共享"
$stdoutLog = Join-Path $logDir "stdout.log"
$stderrLog = Join-Path $logDir "stderr.log"
$port = 8766

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Virtual environment Python is missing: $pythonExe"
}

$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    [pscustomobject]@{
        status = "ALREADY_RUNNING"
        port = $port
        pid = $listener[0].OwningProcess
        endpoint = "http://127.0.0.1:$port/mcp"
    } | ConvertTo-Json
    exit 0
}

New-Item -ItemType Directory -Force -Path $stateDir, $logDir | Out-Null
$process = Start-Process -FilePath $pythonExe `
    -ArgumentList @("-m", "music_material_agent.cli", "mcp-http", "--host", "127.0.0.1", "--port", "$port") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

$deadline = [DateTime]::UtcNow.AddSeconds(15)
do {
    Start-Sleep -Milliseconds 250
    if ($process.HasExited) {
        throw "Shared MCP exited during startup. Check $stderrLog"
    }
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
} until ($listener -or [DateTime]::UtcNow -ge $deadline)

if (-not $listener) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "Shared MCP did not listen on port $port within 15 seconds"
}

$state = [pscustomobject]@{
    launcher_pid = $process.Id
    server_pid = $listener[0].OwningProcess
    started_at = [DateTime]::Now.ToString("o")
    executable = $pythonExe
    project_root = $projectRoot
    endpoint = "http://127.0.0.1:$port/mcp"
    stdout_log = $stdoutLog
    stderr_log = $stderrLog
}
$state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
$state | ConvertTo-Json
