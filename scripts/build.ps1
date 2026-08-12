$ErrorActionPreference = "Stop"

# 平台守卫：此脚本仅适用于 Windows
$runningOnWindows = $true
if (Test-Path variable:IsWindows) { $runningOnWindows = $IsWindows }
if (-not $runningOnWindows) {
    Write-Host "错误：build.ps1 仅适用于 Windows。" -ForegroundColor Red
    Write-Host "在 macOS / Linux 上请使用 scripts/build.sh。" -ForegroundColor Yellow
    exit 1
}

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

$PY = if ($env:PYTHON) { $env:PYTHON } else { "python" }

# 从 pyproject.toml 读取版本号，使产物命名为 cryptobox-<version>.exe
$Version = "0.0.0"
if (Test-Path ".\pyproject.toml") {
    $pyprojectText = Get-Content ".\pyproject.toml" -Raw
    if ($pyprojectText -match '(?m)^\s*version\s*=\s*"([^"]+)"') {
        $Version = $Matches[1]
    }
}
$ExeName = "cryptobox-$Version.exe"

# 若 .venv 不存在则自动创建并安装依赖
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "未检测到 .venv，正在创建虚拟环境并安装依赖..."
    & $PY -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
}

# 编译。先输出到临时目录 dist_build，避免旧 dist\cryptobox.exe 被占用
# （例如 Windows Defender 实时扫描锁文件）导致覆盖失败。
Write-Host "构建 Cryptobox（平台: Windows）..."
if ((Get-Command npm -ErrorAction SilentlyContinue) -and (Test-Path ".\node_modules")) {
    & npm run build:preview
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} elseif (-not (Test-Path ".\src\cryptobox\static\preview-host.js") -or -not (Test-Path ".\src\cryptobox\static\THIRD_PARTY_NOTICES.txt")) {
    Write-Host "错误：缺少网页预览静态包。请安装 Node.js 后运行 npm ci 和 npm run build:preview。" -ForegroundColor Red
    exit 1
}
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm cryptobox.spec --distpath dist_build
if (-not (Test-Path ".\dist_build\$ExeName")) {
    Write-Host "错误：构建失败，未生成 dist_build\$ExeName。" -ForegroundColor Red
    exit 1
}

# 把新产物扶正为 dist；旧 dist 改名存档为 dist.bak。
# 若旧 dist 被占用（服务仍在运行 / 杀毒软件锁定），提示先关闭后重试。
if (Test-Path ".\dist") {
    try {
        if (Test-Path ".\dist.bak") { Remove-Item -Recurse -Force .\dist.bak }
        Move-Item -Path .\dist -Destination .\dist.bak -Force
    } catch {
        Write-Host "无法移动旧 dist（可能被正在运行的服务或杀毒软件锁定）。请先停止 cryptobox 服务后重试。" -ForegroundColor Red
        exit 1
    }
}
Move-Item -Path .\dist_build -Destination .\dist -Force

Write-Host ""
Write-Host "构建完成：dist\$ExeName（版本 $Version）" -ForegroundColor Green
