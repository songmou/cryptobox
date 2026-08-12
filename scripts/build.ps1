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

# 版本冲突检查：dist 中已存在同版本 exe 则中止，保留历史文件不动
if (Test-Path ".\dist\$ExeName") {
    Write-Host "错误：版本文件已存在：dist\$ExeName。" -ForegroundColor Red
    Write-Host "当前 pyproject.toml 版本号为 $Version，请更新版本号后重新打包。" -ForegroundColor Yellow
    exit 1
}

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

# 将新产物并入 dist，保留历史版本的 exe（不再改名存档为 dist.bak）。
# 仍先构建到临时目录 dist_build，避免 PyInstaller --noconfirm 清空整个 dist。
if (-not (Test-Path ".\dist")) {
    New-Item -ItemType Directory -Path ".\dist" | Out-Null
}
try {
    Move-Item -Path ".\dist_build\$ExeName" -Destination ".\dist\$ExeName" -Force
} catch {
    Write-Host "无法写入 dist\$ExeName（可能被正在运行的服务或杀毒软件锁定）。请停止 cryptobox 服务后重试。" -ForegroundColor Red
    Remove-Item -Recurse -Force .\dist_build
    exit 1
}
try {
    Remove-Item -Recurse -Force .\dist_build -ErrorAction Stop
} catch {
    Write-Host "警告：未能删除临时目录 dist_build（可能被杀毒软件锁定），可稍后手动删除。" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "构建完成：dist\$ExeName（版本 $Version）" -ForegroundColor Green
