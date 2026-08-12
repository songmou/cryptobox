# Cryptobox

Cryptobox 是一个本机加密文件浏览器。它递归保护指定目录中的普通文件，并通过仅绑定回环地址的 Web 页面提供文件列表和离线文件预览。

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

程序会自动打开一次性本机访问地址。若不希望自动打开浏览器：

```bash
.venv/bin/cryptobox --root /path/to/vault --no-open
```

首次使用在 Web 中确认目录并设置两次相同的密码，提交后开始初始化和加密。后续启动同样在 Web 中输入密码。

## 网页预览格式

| 类型 | 支持格式 | 说明 |
| --- | --- | --- |
| PDF | `pdf` | 使用浏览器 PDF 阅读器，支持 Range 请求 |
| 图片 | `png`、`jpg`、`jpeg`、`gif`、`webp`、`bmp`、`ico`、`avif`、`svg` | SVG 在无同源权限沙箱中清理后显示 |
| 音视频 | `mp3`、`wav`、`flac`、`m4a`、`aac`、`ogg`、`opus`、`mp4`、`webm`、`mov`、`m4v`、`ogv` | 能否播放取决于浏览器支持的编码 |
| 文本 | 常见文本、代码、配置、JSON、XML、YAML、TOML、INI、无扩展名 UTF-8 文本 | 最大 5 MB；HTML 可安全渲染，脚本和表单会被移除 |
| Markdown / 表格 | `md`、`markdown`、`csv`、`tsv` | Markdown 不执行内嵌 HTML；CSV/TSV 显示为表格 |
| Word | `docx`、`docm`、`dotx` | 宏和嵌入的活动 HTML 不会执行 |
| Excel | `xlsx`、`xlsm`、`xlsb`、`ods` | 支持工作表切换；宏不会执行 |
| PowerPoint | `pptx`、`pptm`、`ppsx` | 支持幻灯片和缩略图；宏不会执行 |
| 电子书 / 压缩包 | `epub`、`zip` | EPUB 按章节显示；ZIP 只列目录，不解压内容 |

Office、EPUB 和 ZIP 的网页预览上限为 50 MB。旧版 `doc`、`xls`、`ppt`、受密码保护的 Office 文件和未知二进制格式不会转换，仍可通过下载按钮导出。

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
.venv/bin/pyinstaller --clean --noconfirm cryptobox.spec
```

输出位于 `dist/cryptobox-<版本>`（如 `dist/cryptobox-0.1.0`）。PyInstaller 不是交叉编译器，Windows、macOS、Linux 必须分别构建。

## 使用边界

- Web 服务固定绑定 `127.0.0.1`，不能作为公网服务使用。
- Web 为只读：可以预览、下载、导出目录、校验和修改密码，不能上传、移动、重命名或删除。
- `.cryptobox` 控制目录、运行中的打包程序和 Cryptobox 临时文件不会被加密。
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
   程序会自动打开浏览器访问一次性本机地址 `127.0.0.1`；`--no-open` 可关闭自动打开。

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
- 关于 iOS：本项目是**桌面**程序（仅绑定 `127.0.0.1` 的 Web 界面），iOS 不适用；在 macOS 上请使用 `scripts/run-dev.sh`。

### 编译（打包为独立可执行文件）

| 平台 | 脚本 | 在项目根目录执行的命令 |
| --- | --- | --- |
| Windows | `scripts/build.ps1` | `powershell -ExecutionPolicy Bypass -File scripts\build.ps1` |
| macOS / Linux | `scripts/build.sh` | `bash scripts/build.sh` |

- 产物：Windows 为 `dist\cryptobox-<版本>.exe`（如 `dist\cryptobox-0.1.0.exe`），macOS / Linux 为 `dist/cryptobox-<版本>`（如 `dist/cryptobox-0.1.0`）（单文件，已内嵌 `static` 资源与依赖），可直接在没有 Python 环境的同平台机器上运行。
- 编译脚本同样带平台守卫：在 macOS / Linux 上误跑 `build.ps1`、或在 Windows 上误跑 `build.sh` 都会提示后退出。
