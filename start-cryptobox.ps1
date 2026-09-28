param(
    [Parameter(Position = 0)]
    [string]$Vault = (Join-Path $env:USERPROFILE "CryptoboxVault"),

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    Write-Host "错误：start-cryptobox.ps1 仅适用于 Windows。" -ForegroundColor Red
    exit 1
}

$ProjectDir = $PSScriptRoot
Set-Location $ProjectDir

$pyprojectText = Get-Content (Join-Path $ProjectDir "pyproject.toml") -Raw
if ($pyprojectText -notmatch '(?m)^\s*version\s*=\s*"([^"]+)"') {
    Write-Host "错误：无法从 pyproject.toml 读取版本号。" -ForegroundColor Red
    exit 1
}

$Version = $Matches[1]
$Binary = Join-Path $ProjectDir "dist\cryptobox-$Version.exe"
if (-not (Test-Path -LiteralPath $Binary -PathType Leaf)) {
    Write-Host "错误：未找到当前版本的 Windows 产物：" -ForegroundColor Red
    Write-Host "  $Binary" -ForegroundColor Red
    Write-Host "请先运行 scripts\build.ps1 构建当前版本。" -ForegroundColor Yellow
    exit 1
}

$binaryTimestamp = (Get-Item -LiteralPath $Binary).LastWriteTimeUtc
$newerSource = Get-ChildItem -Path (Join-Path $ProjectDir "src"), (Join-Path $ProjectDir "cryptobox.spec"), (Join-Path $ProjectDir "pyproject.toml") -File -Recurse |
    Where-Object { $_.LastWriteTimeUtc -gt $binaryTimestamp } |
    Select-Object -First 1
if ($newerSource) {
    Write-Host "警告：源码比 dist 产物新，当前程序可能不包含最近修改。" -ForegroundColor Yellow
    Write-Host "建议更新版本号并重新运行 scripts\build.ps1。" -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "启动 Cryptobox"
Write-Host "  平台   : Windows"
Write-Host "  版本   : $Version"
Write-Host "  入口   : $Binary"
Write-Host "  保险库 : $Vault"
Write-Host ""

& $Binary --root $Vault @ExtraArgs
exit $LASTEXITCODE
