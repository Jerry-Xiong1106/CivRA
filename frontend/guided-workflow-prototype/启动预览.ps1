$ErrorActionPreference = 'Stop'
$taskPython = Get-Command python -ErrorAction SilentlyContinue
$taskLauncher = Get-Command py -ErrorAction SilentlyContinue
$taskBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
Write-Host 'CivRA 本地流程原型'
Write-Host '请在浏览器打开 http://localhost:8782/'
Write-Host '在此窗口按 Ctrl+C 可以停止服务。'
if ($taskLauncher) {
    & $taskLauncher.Source -3 -m http.server 8782 --bind 127.0.0.1 --directory $PSScriptRoot
} elseif ($taskPython -and $taskPython.Source -notlike '*\WindowsApps\*') {
    & $taskPython.Source -m http.server 8782 --bind 127.0.0.1 --directory $PSScriptRoot
} elseif (Test-Path -LiteralPath $taskBundledPython) {
    & $taskBundledPython -m http.server 8782 --bind 127.0.0.1 --directory $PSScriptRoot
} else {
    throw '需要 Python 3。请安装 Python 后重新打开 PowerShell，或在本目录使用可用的 Python 3 命令启动。'
}
if ($LASTEXITCODE -ne 0) { throw '预览服务未启动。请检查 8782 端口是否已有预览服务。' }
