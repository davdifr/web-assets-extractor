# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(SPECPATH).resolve()
yt_dlp_datas = collect_data_files("yt_dlp")
yt_dlp_hiddenimports = collect_submodules("yt_dlp")

a = Analysis(
    ["web_assets_extractor/main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=yt_dlp_datas,
    hiddenimports=yt_dlp_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="web-assets-extractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="web-assets-extractor",
)

app = BUNDLE(
    coll,
    name="web-assets-extractor.app",
    icon=None,
    bundle_identifier="local.web-assets-extractor",
)
