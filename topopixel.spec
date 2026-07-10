# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = []
binaries = []
hiddenimports = []

for pkg in ["rasterio", "pyproj", "geopandas", "fiona", "pyogrio",
            "shapely", "osmnx", "trimesh", "triangle", "scipy", "rtree",
            "manifold3d", "fast_simplification", "vtkmodules"]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

a = Analysis(
    ['main_window.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='topopixel',
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
	icon='icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='topopixel',
)