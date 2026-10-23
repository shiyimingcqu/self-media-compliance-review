$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '请先创建 .venv 并安装 backend/requirements-dev.txt，详见 README.md。'
}
$backendPath = Join-Path $projectRoot 'backend'
$envPath = Join-Path $backendPath '.env'
Push-Location $backendPath
try {
    $arguments = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--reload')
    if (Test-Path -LiteralPath $envPath) { $arguments += @('--env-file', $envPath) }
    & $pythonPath @arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
