# Cryptobox

Cryptobox 是一个本机加密文件浏览器。它递归保护指定目录中的普通文件，并通过仅绑定回环地址的 Web 页面提供文件列表、文本、图片、音频、视频和 PDF 预览。

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

## 测试

```bash
.venv/bin/python -m pytest
```

所有破坏性测试使用 pytest 的系统临时目录，不会访问工作区中的其他数据。

## 打包

```bash
.venv/bin/pyinstaller --clean --noconfirm cryptobox.spec
```

输出位于 `dist/cryptobox`。PyInstaller 不是交叉编译器，Windows、macOS、Linux 必须分别构建。

## 使用边界

- Web 服务固定绑定 `127.0.0.1`，不能作为公网服务使用。
- 首期 Web 为只读：可以预览、下载、导出目录、校验和修改密码，不能上传、移动、重命名或删除。
- `.cryptobox` 控制目录、运行中的打包程序和 Cryptobox 临时文件不会被加密。
- 符号链接不会被跟随；硬链接文件会报告错误并保持原状。

文件格式见 [FORMAT.md](FORMAT.md)，故障处理见 [RECOVERY.md](RECOVERY.md)。
