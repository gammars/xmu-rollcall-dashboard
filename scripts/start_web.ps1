param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$sourceRoot = Join-Path $repoRoot "xmu-rollcall-cli"
$python = "D:\Conda\envs\xmu-rollcall-dashboard\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到现有 Conda 环境：$python"
}
if ($HostAddress -notin @("127.0.0.1", "localhost", "::1") -and -not $env:XMU_ROLLCALL_WEB_PASSWORD) {
    throw "局域网监听前请先设置 `$env:XMU_ROLLCALL_WEB_PASSWORD。"
}

$env:PYTHONPATH = $sourceRoot
$arguments = @("-m", "xmu_rollcall.web", "--host", $HostAddress, "--port", $Port)
if ($Reload) { $arguments += "--reload" }
& $python @arguments
