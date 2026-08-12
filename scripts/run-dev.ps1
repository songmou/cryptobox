$ErrorActionPreference = "Stop"

# 平台守卫：此脚本仅适用于 Windows
$runningOnWindows = $true
if (Test-Path variable:IsWindows) { $runningOnWindows = $IsWindows }
if (-not $runningOnWindows) {
    Write-Host "错误：run-dev.ps1 仅适用于 Windows。" -ForegroundColor Red
    Write-Host "在 macOS / Linux 上请使用 scripts/run-dev.sh。" -ForegroundColor Yellow
    exit 1
}

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

# 保险库目录：第一个参数为 --root，否则使用默认
$Vault = if ($args.Count -ge 1) { $args[0] } else { "D:\Kaung\cryptofile" }

Write-Host "启动 Cryptobox"
Write-Host "  平台   : Windows"
Write-Host "  保险库 : $Vault"

# 选择入口：优先 dist 编译产物（按 pyproject.toml 版本号定位），其次 venv，最后 python -m
$Version = "0.0.0"
if (Test-Path ".\pyproject.toml") {
    $pyprojectText = Get-Content ".\pyproject.toml" -Raw
    if ($pyprojectText -match '(?m)^\s*version\s*=\s*"([^"]+)"') {
        $Version = $Matches[1]
    }
}
$DistExe = "dist\cryptobox-$Version.exe"
if (Test-Path $DistExe) {
    Write-Host "  入口   : $DistExe"
    & $DistExe --root $Vault
} elseif (Test-Path ".venv\Scripts\cryptobox.exe") {
    Write-Host "  入口   : .venv\Scripts\cryptobox.exe"
    & ".venv\Scripts\cryptobox.exe" --root $Vault
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    Write-Host "  入口   : python -m cryptobox.main"
    python -m cryptobox.main --root $Vault
} else {
    Write-Host "错误：未找到 Python 且未构建 cryptobox，请先运行 scripts\build.ps1。" -ForegroundColor Red
    exit 1
}
exit $LASTEXITCODE

