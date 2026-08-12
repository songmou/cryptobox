# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import tomllib

from PyInstaller.utils.hooks import collect_submodules

# --- Version resource (single source of truth: pyproject.toml) ---------------
# Read the release version from pyproject.toml so the compiled binary, the
# package __version__, and the web UI footer never drift apart.
with open(os.path.join(SPECPATH, "pyproject.toml"), "rb") as _pyproject_file:
    _pkg = tomllib.load(_pyproject_file)
VERSION = str(_pkg["project"]["version"])
_parts = [int(x) for x in VERSION.split(".")]
while len(_parts) < 3:
    _parts.append(0)
_filevers = tuple(_parts[:3] + [0])  # (major, minor, patch, build)

# Only Windows embeds a version resource; on other platforms `version=` is
# ignored by PyInstaller, so we guard generation to keep cross-platform CI green.
# We pass the VSVersionInfo object directly (PyInstaller also accepts a file
# path, but the object avoids any temp-file bookkeeping).
version_info = None
if sys.platform == "win32":
    try:
        from PyInstaller.utils.win32.versioninfo import (
            VSVersionInfo,
            FixedFileInfo,
            StringFileInfo,
            StringTable,
            StringStruct,
            VarFileInfo,
            VarStruct,
        )

        version_info = VSVersionInfo(
            ffi=FixedFileInfo(
                filevers=_filevers,
                prodvers=_filevers,
                mask=0x3F,
                flags=0x0,
                OS=0x40004,
                fileType=0x1,
                subtype=0x0,
                date=(0, 0),
            ),
            kids=[
                StringFileInfo(
                    [
                        StringTable(
                            "040904B0",
                            [
                                StringStruct("CompanyName", "Cryptobox"),
                                StringStruct("FileDescription", "Cryptobox encrypted-file browser"),
                                StringStruct("FileVersion", VERSION),
                                StringStruct("InternalName", "cryptobox"),
                                StringStruct("LegalCopyright", "Copyright (c) Cryptobox"),
                                StringStruct("OriginalFilename", f"cryptobox-{VERSION}.exe"),
                                StringStruct("ProductName", "Cryptobox"),
                                StringStruct("ProductVersion", VERSION),
                            ],
                        )
                    ]
                ),
                VarFileInfo([VarStruct("Translation", [1033, 1200])]),
            ],
        )
    except Exception as _vi_error:  # pragma: no cover
        print(f"[cryptobox] version resource skipped: {_vi_error}")
        version_info = None

hiddenimports = collect_submodules("uvicorn")

a = Analysis(
    ["cryptobox_entry.py"],
    pathex=["src"],
    binaries=[],
    # Bundle the same project metadata used above so frozen applications report
    # the exact version that also determines their executable filename.
    datas=[
        ("src/cryptobox/static", "cryptobox/static"),
        ("pyproject.toml", "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"cryptobox-{VERSION}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_info,
)
