# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Video Life — cross-platform build.

Usage:
  pyinstaller video_life.spec

Output: dist/Video Life/   (onedir bundle)
"""

import sys
import os
from pathlib import Path

block_cipher = None

_MACOS   = sys.platform == 'darwin'
_WINDOWS = sys.platform == 'win32'
_LINUX   = sys.platform.startswith('linux')

# ── Data files ───────────────────────────────────────────────────────────────
# (src, dest_dir_in_bundle)
datas = [
    ('presets', 'presets'),
]

# ── Hidden imports ────────────────────────────────────────────────────────────
hidden_imports = [
    # PyQt6 subsystems
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'PyQt6.QtOpenGL',
    'PyQt6.QtOpenGLWidgets',
    'PyQt6.QtMultimedia',
    'PyQt6.QtMultimediaWidgets',
    # OpenGL
    'OpenGL',
    'OpenGL.GL',
    'OpenGL.GL.shaders',
    'OpenGL.arrays.numpymodule',
    # Audio
    'sounddevice',
    'soundfile',
    'scipy.signal',
    'scipy.fft',
    # MIDI
    'mido',
    'mido.backends.rtmidi',
    # Misc
    'numpy',
    'cv2',
    'ctypes',
    'multiprocessing',
]

if _MACOS:
    hidden_imports += [
        'objc',
        'Foundation',
        'AVFoundation',
        'CoreMedia',
        'Quartz',
        'Quartz.CoreVideo',
    ]

# ── Excluded modules (reduce bundle size) ─────────────────────────────────────
excludes = [
    'tkinter',
    'matplotlib',
    'IPython',
    'jupyter',
    'PIL',
    'wx',
    'gtk',
]

if not _MACOS:
    excludes += [
        'objc',
        'Foundation',
        'AVFoundation',
        'CoreMedia',
        'Quartz',
    ]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Collect all PyQt6 plugins (platform plugins, imageformats, multimedia)
from PyInstaller.utils.hooks import collect_all
qt_datas, qt_binaries, qt_hiddenimports = collect_all('PyQt6')
a.datas    += qt_datas
a.binaries += qt_binaries
a.hiddenimports += qt_hiddenimports

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Video Life',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can break Qt plugins
    console=False,      # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Video Life',
)

# ── macOS: wrap in .app bundle ────────────────────────────────────────────────
if _MACOS:
    app = BUNDLE(
        coll,
        name='Video Life.app',
        icon=None,            # TODO: add icon.icns
        bundle_identifier='com.videolife.app',
        info_plist={
            'CFBundleDisplayName': 'Video Life',
            'CFBundleShortVersionString': '1.0.0',
            'NSCameraUsageDescription':
                'Video Life uses your camera for live video synthesis.',
            'NSMicrophoneUsageDescription':
                'Video Life uses audio input for reactive synthesis.',
            'NSHighResolutionCapable': True,
        },
    )
