# Cryptobox

Cryptobox 是一个加密文件浏览器。它递归保护指定目录中的普通文件，并通过本机 HTTP 或直接启用 TLS 的 Web 页面提供文件列表和离线文件预览。

## 重要警告

- 忘记密码后数据无法恢复。首次使用前必须保留独立备份。
- 不要把数据库、持续写入的日志、正在运行的程序目录或同步工具工作目录作为保险库。
- 原子替换不等于物理安全擦除；SSD、文件系统快照和云同步可能保留旧明文数据块。敏感设备应同时启用 FileVault、BitLocker 或 LUKS。
- 当前版本保留文件名、目录结构、文件大小近似信息和修改时间，保护的是文件内容。
- 保险库主密钥由密码和文件头中的保险库 ID 直接派生；`vault.json` 丢失时可由任意完整加密文件和正确密码重建。

## 开发运行

要求 Python 3.11 以上，推荐 Python 3.13：

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/cryptobox --root /path/to/dedicated/vault
```

程序会自动打开本机页面；未初始化时打开的是一次性初始化地址。若不希望自动打开浏览器：

```bash
.venv/bin/cryptobox --root /path/to/vault --no-open
```

默认仍只监听 `127.0.0.1`。初始化后的保险库会显示密码登录页；尚未初始化时，必须使用控制台打印的、15 分钟有效的一次性初始化 URL。

## HTTPS 与远程访问

监听任何非回环地址时，Cryptobox 强制要求 HTTPS 和明确的公开 URL。使用已有证书：

```bash
cryptobox --root /path/to/vault \
  --host 0.0.0.0 --port 8787 \
  --public-url https://203.0.113.10:8787 \
  --tls-cert /path/to/fullchain.pem \
  --tls-key /path/to/privkey.pem \
  --no-open
```

证书的 SAN 必须包含 `--public-url` 中的域名或 IP。若同一服务还通过其他地址访问，可重复传入 `--allowed-origin https://host:port`，证书也必须覆盖这些地址。

测试、内网或已能安全分发信任证书的环境可以使用持久化自签名证书：

```bash
cryptobox --root /path/to/vault \
  --host 0.0.0.0 \
  --public-url https://192.168.1.20:8787 \
  --self-signed --no-open
```

自签名证书保存在系统用户配置目录，程序会打印 SHA-256 指纹。必须通过另一条可信渠道核对指纹并把证书安装为可信证书；公网使用时不要直接忽略浏览器警告，否则无法防止中间人攻击。

可选来源白名单接受单个 IP 或 CIDR，可重复指定；回环地址始终允许：

```bash
cryptobox --root /path/to/vault \
  --host 0.0.0.0 \
  --public-url https://vault.example.com:8787 \
  --tls-cert /path/to/fullchain.pem --tls-key /path/to/privkey.pem \
  --allow-client 198.51.100.24 \
  --allow-client 10.20.0.0/16
```

Cryptobox 直接使用 TCP 对端地址并忽略 `X-Forwarded-For`；当前模式不支持在反向代理后恢复真实客户端 IP。应用不会修改系统防火墙、路由器端口映射、云安全组或 DNS。

公网登录保护规则如下：

- 同一 IP 在 5 分钟内失败 5 次，封禁登录 15 分钟。
- 全局在 5 分钟内失败 30 次，封禁所有登录 15 分钟。
- Argon2 密码验证串行执行；封禁请求不会进入昂贵的密码计算。
- 仅保留一个活动登录会话；新设备登录会使旧设备会话失效。
- 安全日志记录登录、封禁及被拒绝的 IP，但不会记录密码、Cookie、令牌或完整查询 URL。

封禁状态只保存在进程内，重启后清除。公网部署应使用长且唯一的密码短语，并优先结合 VPN、来源白名单和主机防火墙。

首次使用在 Web 中确认目录并设置两次相同的密码，提交后开始初始化和加密。后续启动同样在 Web 中输入密码。Cryptobox 会在系统用户配置目录中只记录上次打开的保险库路径；未传 `--root` 时自动恢复该目录，显式 `--root` 始终优先。设置文件不包含密码、密钥或文件内容。

解锁后可通过侧栏顶部的“切换文件夹”按钮输入另一个绝对路径。切换时当前保险库会先安全结束操作并锁定，再显示新目录的创建或解锁页面。

## 网页预览格式

| 类型 | 支持格式 | 说明 |
| --- | --- | --- |
| PDF | `pdf` | 使用浏览器 PDF 阅读器，支持 Range 请求 |
| 图片 | `png`、`jpg`、`jpeg`、`gif`、`webp`、`bmp`、`ico`、`avif`、`heic`、`heif`、`svg` | HEIC/HEIF 能否显示取决于浏览器原生解码支持；SVG 在无同源权限沙箱中清理后显示 |
| 音视频 | `mp3`、`wav`、`flac`、`m4a`、`aac`、`ogg`、`opus`、`mp4`、`webm`、`mov`、`m4v`、`ogv` | 能否播放取决于浏览器支持的编码 |
| 文本 | 常见文本、代码、配置、JSON、XML、YAML、TOML、INI、无扩展名 UTF-8 文本 | 最大 5 MB；HTML 可安全渲染，脚本和表单会被移除 |
| Markdown / 表格 | `md`、`markdown`、`csv`、`tsv` | Markdown 不执行内嵌 HTML；CSV/TSV 显示为表格 |
| Word | `docx`、`docm`、`dotx` | 宏和嵌入的活动 HTML 不会执行 |
| Excel | `xlsx`、`xlsm`、`xlsb`、`ods` | 支持工作表切换；宏不会执行 |
| PowerPoint | `pptx`、`pptm`、`ppsx` | 支持幻灯片和缩略图；宏不会执行 |
| 电子书 / 压缩包 | `epub`、`zip` | EPUB 按章节显示；ZIP 只列目录，不解压内容 |

Office、EPUB 和 ZIP 的网页预览上限为 50 MB。旧版 `doc`、`xls`、`ppt`、受密码保护的 Office 文件和未知二进制格式不会转换，仍可通过下载按钮导出。对于没有安全网页预览器的格式，可以点击“尝试以文本打开”；该功能只接受最大 5 MB、无 NUL 字节的有效 UTF-8 文本，二进制数据不会被强制显示。

## 测试

```bash
.venv/bin/python -m pytest
```

所有破坏性测试使用 pytest 的系统临时目录，不会访问工作区中的其他数据。

## 打包

预览依赖已编译到 `src/cryptobox/static/preview-host.js` 并提交到仓库，运行和普通 PyInstaller 打包不需要 Node.js。修改 `preview-host-src.js` 或升级预览依赖时，需要 Node.js 20 以上并重新生成静态包：

```bash
npm ci
npm run build:preview
```

```bash
.venv/bin/python -m PyInstaller --clean --noconfirm cryptobox.spec
```

输出位于 `dist/cryptobox-<版本>`（如 `dist/cryptobox-0.1.0`）。PyInstaller 不是交叉编译器，Windows、macOS、Linux 必须分别构建。

## 使用边界

- 默认 Web 服务绑定 `127.0.0.1`；非回环监听必须显式配置 HTTPS、公开 URL 和证书。
- Web 为只读：可以预览、下载、导出目录、校验和修改密码，不能上传、移动、重命名或删除。
- 文件列表显示文件类型图标以及“已加密 / 未加密”状态；未加密或加密失败的文件不会通过预览、下载或导出接口读取。
- `.cryptobox` 控制目录、当前正在运行的 Cryptobox 可执行文件和 Cryptobox 临时文件不会被加密。只排除当前可执行文件，不会排除它所在目录中的其他文件。
- 符号链接不会被跟随；硬链接文件会报告错误并保持原状。

文件格式见 [FORMAT.md](FORMAT.md)，故障处理见 [RECOVERY.md](RECOVERY.md)。

## Windows 启动教程（从源码运行）

要求 Python 3.11 以上（推荐 3.13）。以下操作在 PowerShell 中进行。

1. 在项目根目录创建虚拟环境：
   ```powershell
   python -m venv .venv
   ```
   > 若系统 Python 缺少 `venv` 模块（部分精简 / 嵌入式安装会出现 `No module named venv`），请改用完整版 Python 3.13，或用其他软件自带的 managed Python：
   > `"C:\Users\abc\python\versions\3.13.12\python.exe" -m venv .venv`

2. 激活虚拟环境（PowerShell 的脚本是 `Activate.ps1`，不是 `activate`）：
   ```powershell
   & .\.venv\Scripts\Activate.ps1
   ```
   > 若被执行策略拦截，先运行：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force`

3. 安装项目（含开发依赖）：
   ```powershell
   pip install -e ".[dev]"
   ```

4. 启动（必须指定专用保险库目录，示例为 `D:\cryptofile`）：
   ```powershell
   cryptobox --root "D:\cryptofile"
   ```
   程序会自动打开浏览器访问本机地址 `127.0.0.1`；未初始化时使用一次性初始化 URL，`--no-open` 可关闭自动打开。

> 不激活也可直接调用可执行入口：
> `D:\cryptobox\.venv\Scripts\cryptobox.exe --root "D:\cryptofile"`

首次使用在 Web 中确认目录并设置两次相同的密码，提交后开始初始化与加密；后续启动同样在 Web 中输入密码。

## 编译（打包为独立可执行文件）

使用 PyInstaller，配置见 `cryptobox.spec`：

```powershell
.venv\Scripts\pyinstaller --clean --noconfirm cryptobox.spec
```

输出位于 `dist\cryptobox-<版本>.exe`（如 `dist\cryptobox-0.1.0.exe`）（单文件模式，已内嵌 `src/cryptobox/static` 资源与 OpenSSL 等依赖），可直接在没有 Python 环境的 Windows 上运行：

```powershell
dist\cryptobox-0.1.0.exe --root "D:\cryptofile"
```

### Windows / macOS / Linux 编译差异

- **同一份 `cryptobox.spec` 三平台通用**，spec 内部没有平台分支（入口统一为 `cryptobox_entry.py`，打包 `static` 资源，隐藏导入 `uvicorn` 子模块）。
- **PyInstaller 不是交叉编译器**，必须在目标平台上各自构建一次：Windows 上产出 `.exe`，macOS / Linux 上产出同名终端可执行文件。无法在一台机器上打出另外两个平台的产品。
- **Windows 产物为控制台程序**（`console=True`），运行时会保留一个命令行窗口用于日志输出。
- **macOS / Linux 产物未经代码签名**（`codesign_identity=None`、`entitlements_file=None`）。macOS 上首次打开可能被 Gatekeeper 拦截，需要在「系统设置 → 隐私与安全性」中手动放行，或从终端运行。
- 各平台分别构建后即可分发对应平台的独立可执行文件。

## 一键脚本（启动与编译）

项目在 `scripts/` 下提供了跨平台的一键脚本，已内置**平台守卫**：在错误的平台上运行会提示并退出，不会误执行。脚本会自动选择运行入口（优先 `dist/` 下的编译产物，其次 `.venv` 虚拟环境入口，最后回退到 `python -m cryptobox.main`）；编译脚本在缺少 `.venv` 时会自动创建并安装依赖。

### 直接启动 dist 产物

项目根目录提供了只启动当前版本 `dist` 产物的快捷脚本，不会回退到虚拟环境或源码：

| 平台 | 双击入口 | 命令行入口 |
| --- | --- | --- |
| Windows | `start-cryptobox.cmd` | `start-cryptobox.cmd "D:\my\vault"` |
| macOS | `start-cryptobox.command` | `./start-cryptobox.command /path/to/vault` |

- 不传目录时，默认使用当前用户主目录下的 `CryptoboxVault`。
- 第一个参数是保险库目录，后续参数会原样传给 Cryptobox。
- 脚本严格按 `pyproject.toml` 的版本号选择 `dist/cryptobox-<版本>`，避免误启动无版本名的旧产物。
- 如果源码比 `dist` 产物新，脚本会明确警告需要更新版本号并重新构建。
- macOS 如果阻止首次运行，请在“系统设置 → 隐私与安全性”中确认程序来源；脚本不会自动移除隔离属性。

### 启动

| 平台 | 脚本 | 在项目根目录执行的命令 |
| --- | --- | --- |
| Windows | `scripts/run-dev.ps1` | `powershell -ExecutionPolicy Bypass -File scripts\run-dev.ps1` |
| macOS / Linux | `scripts/run-dev.sh` | `bash scripts/run-dev.sh` |

- 第一个参数即保险库目录（`--root`）；不传则使用默认值（Windows 默认 `D:\Kaung\cryptofile`，macOS / Linux 默认 `~/cryptofile`，可在脚本顶部修改）。
  ```powershell
  # Windows，指定自定义保险库
  powershell -ExecutionPolicy Bypass -File scripts\run-dev.ps1 "D:\my\vault"
  ```
  ```bash
  # macOS / Linux，指定自定义保险库
  bash scripts/run-dev.sh /path/to/vault
  ```
- iOS 不能运行 Cryptobox 服务端，但可以作为远程 HTTPS 客户端访问运行在桌面或服务器上的实例。自签名模式需要先在 iOS 中正确安装并信任证书。
- 保险库目录后的其他参数会原样传给 Cryptobox，例如：`bash scripts/run-dev.sh /path/to/vault --host 0.0.0.0 --public-url https://192.168.1.20:8787 --self-signed --no-open`。

### 编译（打包为独立可执行文件）

| 平台 | 脚本 | 在项目根目录执行的命令 |
| --- | --- | --- |
| Windows | `scripts/build.ps1` | `powershell -ExecutionPolicy Bypass -File scripts\build.ps1` |
| macOS / Linux | `scripts/build.sh` | `bash scripts/build.sh` |

- 产物：Windows 为 `dist\cryptobox-<版本>.exe`（如 `dist\cryptobox-0.1.0.exe`）以及对应的 `.exe.sha256` 校验文件，macOS / Linux 为 `dist/cryptobox-<版本>`（如 `dist/cryptobox-0.1.0`）（单文件，已内嵌 `static` 资源与依赖），可直接在没有 Python 环境的同平台机器上运行。
- 编译脚本同样带平台守卫：在 macOS / Linux 上误跑 `build.ps1`、或在 Windows 上误跑 `build.sh` 都会提示后退出。
- Windows 构建会同步 `.[dev]` 依赖、输出 Python/PyInstaller/关键依赖和 Git commit、运行完整测试，再使用 PyInstaller `--clean` 打包。打包后会记录 SHA256、显示 Authenticode 状态，并在系统提供 Microsoft Defender PowerShell 模块时扫描最终 EXE。

### Windows Defender 误报排查

PyInstaller 单文件程序可能被安全软件的启发式或机器学习规则误报，但检测名称本身不能证明文件安全。Cryptobox 还会在用户明确选择目录后递归加密文件，因此只有从受控源码和干净依赖环境构建、测试并核对哈希后，才能按误报流程处理。

如果 PowerShell 提示“文件包含病毒或潜在的垃圾软件”，不要关闭 Defender，也不要把项目目录、保险库目录、用户目录或整个磁盘加入排除项。先在管理员 PowerShell 中更新安全情报并读取最近检测：

```powershell
Update-MpSignature

$d = Get-MpThreatDetection |
  Where-Object { $_.Resources -match 'cryptobox-.*\.exe' } |
  Sort-Object InitialDetectionTime -Descending |
  Select-Object -First 1

$d | Format-List ThreatID,InitialDetectionTime,ActionSuccess,Resources
Get-MpThreat -ThreatID $d.ThreatID |
  Format-List ThreatName,SeverityID,CategoryID,DidThreatExecute,IsActive
```

如果当前位于 `dist` 目录，查询上级虚拟环境时路径必须包含 `..\`：

```powershell
& ..\.venv\Scripts\python.exe -m PyInstaller --version
& ..\.venv\Scripts\python.exe -m pip show pyinstaller pyinstaller-hooks-contrib cryptography
```

若怀疑旧虚拟环境或旧 PyInstaller 缓存，先回到项目根目录，保留旧环境并创建全新环境，再运行构建脚本：

```powershell
Set-Location F:\cryptosoft\cryptobox
Rename-Item .venv ".venv-old-$(Get-Date -Format yyyyMMdd-HHmmss)"
py -3.13 -m venv .venv
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

干净构建仍被检测时，只恢复这一份新构建的 EXE用于提交分析，不要执行旧产物。通过 [Microsoft Security Intelligence 文件分析入口](https://www.microsoft.com/en-us/wdsi/filesubmission)选择 **Software developer**，提交 EXE、检测名称、`.sha256`、PyInstaller 版本、Git commit、项目用途和复现步骤。等待 Microsoft 最终判定并更新 Defender 安全情报后重新扫描。PyInstaller 官方同样建议将误报提交给对应安全软件厂商，随机改代码或反复换版本不能稳定解决问题：<https://github.com/pyinstaller/pyinstaller/blob/develop/.github/ISSUE_TEMPLATE/antivirus.md>。

当前 Windows 产物没有 Authenticode 签名。仅在自己的机器上使用时无需购买公共代码签名证书；自签名证书也不会自动获得 SmartScreen 信誉。未来公开分发时，应使用 Microsoft Artifact Signing 或受信任的代码签名证书，并保持连续发布使用同一发布者身份：<https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation>。
