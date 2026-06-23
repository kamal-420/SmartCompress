# -*- mode: python ; coding: utf-8 -*-

import sys
import sysconfig
from pathlib import Path

import PyInstaller
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, tcl_tk


python_base = Path(sys.base_prefix)
stdlib_dir = Path(sysconfig.get_paths()['stdlib'])
python_dlls = python_base / 'DLLs'
tcl_root = python_base / 'tcl'
project_root = Path.cwd()

# This Python install has working tkinter files, DLLs, and Tcl/Tk runtime data,
# but PyInstaller's Tcl/Tk probe reports available=False and its pre-find hook
# then excludes tkinter from analysis. Keep the normal analysis path enabled and
# provide the Tcl/Tk runtime files explicitly below.
tcl_tk.tcltk_info.available = True
tcl_tk.tcltk_info.data_files = []


explicit_hiddenimports = [
    '_tkinter',
    'tkinter',
    'tkinter.ttk',
    'tkinter.filedialog',
    'tkinter.messagebox',
    'tkinterdnd2',
]

hiddenimports = sorted(set(
    explicit_hiddenimports
    + collect_submodules('tkinter')
    + collect_submodules('tkinterdnd2')
    + collect_submodules('customtkinter')
    + collect_submodules('PIL')
))

datas = [
    ('smartcompress_logo.png', '.'),
    ('icon.ico', '.'),
    ('settings.json', '.'),
    ('history.json', '.'),
    ('README.txt', '.'),
]
datas += collect_data_files('tkinter', include_py_files=True)
datas += collect_data_files('tkinterdnd2')
datas += collect_data_files('customtkinter')

if (tcl_root / 'tcl8.6').exists():
    datas += [
        (str(path), str(Path('_tcl_data') / path.relative_to(tcl_root / 'tcl8.6').parent))
        for path in (tcl_root / 'tcl8.6').rglob('*')
        if path.is_file()
    ]

if (tcl_root / 'tk8.6').exists():
    datas += [
        (str(path), str(Path('_tk_data') / path.relative_to(tcl_root / 'tk8.6').parent))
        for path in (tcl_root / 'tk8.6').rglob('*')
        if path.is_file()
    ]

tkinter_binaries = [
    (str(python_dlls / '_tkinter.pyd'), '.'),
    (str(python_dlls / 'tcl86t.dll'), '.'),
    (str(python_dlls / 'tk86t.dll'), '.'),
]
tkinter_binaries = [(src, dest) for src, dest in tkinter_binaries if Path(src).exists()]

tkinter_runtime_hook = str(
    Path(PyInstaller.__file__).resolve().parent
    / 'hooks'
    / 'rthooks'
    / 'pyi_rth__tkinter.py'
)

ffmpeg_names = ['ffmpeg.exe', 'ffplay.exe', 'ffprobe.exe']
ffmpeg_binaries = []
for name in ffmpeg_names:
    candidates = [
        project_root / name,
        project_root / 'ffmpeg' / name,
    ]
    source = next((path for path in candidates if path.exists()), None)
    if source is None:
        raise FileNotFoundError(
            f'{name} must exist in the project root or in the ffmpeg folder.'
        )
    ffmpeg_binaries.append((str(source), '.'))


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=ffmpeg_binaries + tkinter_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[tkinter_runtime_hook],
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
    name='SmartCompress',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
    version='version_info.txt',
    contents_directory='.',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SmartCompress_v2.2',
)
