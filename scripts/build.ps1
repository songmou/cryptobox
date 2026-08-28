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
$VenvPython = ".\.venv\Scripts\python.exe"
$DistBuildDir = ".\dist_build"
$WorkBuildDir = ".\build_build"

function Show-DefenderDiagnostics {
    param([string]$ArtifactName)

    Write-Host "Windows Defender 可能已隔离或删除 $ArtifactName。" -ForegroundColor Yellow
    Write-Host "请在管理员 PowerShell 中执行以下命令查看具体检测：" -ForegroundColor Yellow
    Write-Host 'Get-MpThreatDetection | Sort-Object InitialDetectionTime -Descending | Select-Object -First 5 | Format-List ThreatID,InitialDetectionTime,ActionSuccess,Resources'
    Write-Host 'Get-MpThreat | Format-List ThreatName,SeverityID,CategoryID,DidThreatExecute,IsActive'
    Write-Host "不要关闭实时保护，也不要为项目目录或磁盘添加排除项。" -ForegroundColor Yellow
    Write-Host "误报提交：https://www.microsoft.com/en-us/wdsi/filesubmission" -ForegroundColor Cyan
}

function Remove-BuildDirectory {
    param([string]$Path)

    if (-not (Test-Path $Path)) { return }
    try {
        Remove-Item -Recurse -Force $Path -ErrorAction Stop
    } catch {
        Write-Host "警告：未能删除临时目录 $Path（可能被杀毒软件锁定），可稍后手动删除。" -ForegroundColor Yellow
    }
}

# 版本冲突检查：dist 中已存在同版本 exe 则中止，保留历史文件不动
if (Test-Path ".\dist\$ExeName") {
    Write-Host "错误：版本文件已存在：dist\$ExeName。" -ForegroundColor Red
    Write-Host "当前 pyproject.toml 版本号为 $Version，请更新版本号后重新打包。" -ForegroundColor Yellow
    exit 1
}

# 若 .venv 不存在则自动创建；每次构建都同步依赖，避免沿用过期构建环境。
if (-not (Test-Path $VenvPython)) {
    Write-Host "未检测到 .venv，正在创建虚拟环境..."
    & $PY -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "正在同步 Python 构建依赖..."
& $VenvPython -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "构建环境：" -ForegroundColor Cyan
& $VenvPython --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython -m PyInstaller --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython -m pip show pyinstaller pyinstaller-hooks-contrib cryptography
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Host "Git commit："
    & git rev-parse HEAD
    Write-Host "Git 工作区状态："
    & git status --short
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

Write-Host "正在运行完整测试..."
& $VenvPython -m pytest
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误：测试失败，已取消打包。" -ForegroundColor Red
    exit $LASTEXITCODE
}

& $VenvPython -m PyInstaller --clean --noconfirm cryptobox.spec --distpath $DistBuildDir --workpath $WorkBuildDir
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误：PyInstaller 构建失败；如果日志显示产物被移除，请检查 Defender 检测记录。" -ForegroundColor Red
    Show-DefenderDiagnostics $ExeName
    Remove-BuildDirectory $DistBuildDir
    Remove-BuildDirectory $WorkBuildDir
    exit $LASTEXITCODE
}
if (-not (Test-Path "$DistBuildDir\$ExeName")) {
    Write-Host "错误：构建失败，未生成 dist_build\$ExeName。" -ForegroundColor Red
    Show-DefenderDiagnostics $ExeName
    Remove-BuildDirectory $DistBuildDir
    Remove-BuildDirectory $WorkBuildDir
    exit 1
}

# 将新产物并入 dist，保留历史版本的 exe（不再改名存档为 dist.bak）。
# 仍先构建到临时目录 dist_build，避免 PyInstaller --noconfirm 清空整个 dist。
if (-not (Test-Path ".\dist")) {
    New-Item -ItemType Directory -Path ".\dist" | Out-Null
}
try {
    Move-Item -Path "$DistBuildDir\$ExeName" -Destination ".\dist\$ExeName" -Force
} catch {
    Write-Host "无法写入 dist\$ExeName（可能被正在运行的服务或杀毒软件锁定）。请停止 cryptobox 服务后重试。" -ForegroundColor Red
    Show-DefenderDiagnostics $ExeName
    Remove-BuildDirectory $DistBuildDir
    Remove-BuildDirectory $WorkBuildDir
    exit 1
}

$FinalExePath = ".\dist\$ExeName"
if (-not (Test-Path $FinalExePath)) {
    Write-Host "错误：产物移动后不存在。" -ForegroundColor Red
    Show-DefenderDiagnostics $ExeName
    Remove-BuildDirectory $DistBuildDir
    Remove-BuildDirectory $WorkBuildDir
    exit 1
}

$Hash = Get-FileHash -LiteralPath $FinalExePath -Algorithm SHA256
$HashPath = "$FinalExePath.sha256"
$HashLine = "$($Hash.Hash.ToLowerInvariant())  $ExeName"
Set-Content -LiteralPath $HashPath -Value $HashLine -Encoding Ascii
Write-Host "SHA256：$($Hash.Hash)" -ForegroundColor Cyan
Write-Host "校验文件：dist\$ExeName.sha256"

$Signature = Get-AuthenticodeSignature -LiteralPath $FinalExePath
Write-Host "Authenticode：$($Signature.Status)"
if ($Signature.SignerCertificate) {
    Write-Host "签名者：$($Signature.SignerCertificate.Subject)"
} else {
    Write-Host "提示：当前产物未签名；仅自用时可保留此状态。" -ForegroundColor Yellow
}

if (Get-Command Start-MpScan -ErrorAction SilentlyContinue) {
    Write-Host "正在使用 Microsoft Defender 扫描最终 EXE..."
    try {
        $ResolvedExePath = (Resolve-Path -LiteralPath $FinalExePath).Path
        Start-MpScan -ScanType CustomScan -ScanPath $ResolvedExePath -ErrorAction Stop
        Start-Sleep -Seconds 2
    } catch {
        Write-Host "警告：无法启动 Microsoft Defender 自定义扫描：$($_.Exception.Message)" -ForegroundColor Yellow
    }
    if (-not (Test-Path $FinalExePath)) {
        Write-Host "错误：Microsoft Defender 已在扫描期间移除最终 EXE。" -ForegroundColor Red
        Show-DefenderDiagnostics $ExeName
        Remove-BuildDirectory $DistBuildDir
        Remove-BuildDirectory $WorkBuildDir
        exit 1
    }
} else {
    Write-Host "提示：系统未提供 Start-MpScan，请使用当前安全软件手动扫描最终 EXE。" -ForegroundColor Yellow
}

Remove-BuildDirectory $DistBuildDir
Remove-BuildDirectory $WorkBuildDir

Write-Host ""
Write-Host "构建完成：dist\$ExeName（版本 $Version）" -ForegroundColor Green
