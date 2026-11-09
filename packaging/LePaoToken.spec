# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/Users/xi/Documents/校园乐跑/capture/capture_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('/Users/xi/Documents/校园乐跑/capture/token_addon.py', '.')],
    hiddenimports=[],
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
    name='LePaoToken',
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
    name='LePaoToken',
)
app = BUNDLE(
    coll,
    name='LePaoToken.app',
    icon=None,
    bundle_identifier=None,
)
