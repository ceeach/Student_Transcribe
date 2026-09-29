# -*- mode: python ; coding: utf-8 -*-

import sys
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

block_cipher = None

# Collect all data, binaries, and hidden imports for speech and audio engines
fw_datas, fw_binaries, fw_hidden = collect_all('faster_whisper')
ct_datas, ct_binaries, ct_hidden = collect_all('ctranslate2')
sd_datas, sd_binaries, sd_hidden = collect_all('sounddevice')
sf_datas, sf_binaries, sf_hidden = collect_all('soundfile')
nc_datas, nc_binaries, nc_hidden = collect_all('notion_client')
so_datas, so_binaries, so_hidden = collect_all('sherpa_onnx')
pa_datas, pa_binaries, pa_hidden = collect_all('pyaudiowpatch')
av_datas, av_binaries, av_hidden = collect_all('av')
fl_datas, fl_binaries, fl_hidden = collect_all('qfluentwidgets')
fw2_datas, fw2_binaries, fw2_hidden = collect_all('qframelesswindow')
dd_datas, dd_binaries, dd_hidden = collect_all('darkdetect')

all_datas = [
    ('assets', 'assets'),
] + fw_datas + ct_datas + sd_datas + sf_datas + nc_datas + so_datas + pa_datas + av_datas + fl_datas + fw2_datas + dd_datas

all_binaries = fw_binaries + ct_binaries + sd_binaries + sf_binaries + so_binaries + pa_binaries + av_binaries + fl_binaries + fw2_binaries + dd_binaries

all_hiddenimports = [
    'PyQt6',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'dotenv',
    'numpy',
    'ctypes',
    'ctypes.windll',
    'diarization',
    'video_watcher',
    'audio_recorder',
    'transcript_diff',
    'benchmark',
    'ui.benchmark_dialog',
    'transcript_storage',
    'qfluentwidgets',
    'qframelesswindow',
    'darkdetect',
    'colorthief',
] + fw_hidden + ct_hidden + sd_hidden + sf_hidden + nc_hidden + so_hidden + pa_hidden + av_hidden + fl_hidden + fw2_hidden + dd_hidden

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NotionTranscribe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='NotionTranscribe',
)
