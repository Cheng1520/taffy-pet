# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

    python -m PyInstaller taffy-pet.spec

产出 dist/TaffyPet/，再由 installer/taffy-pet.iss 包成安装程序。

**为什么用 onedir 不用 onefile**：onefile 每次启动都要把几十 MB 解压到临时目录，
PyQt5 这么大，冷启动肉眼可见地慢。反正外面还套一层安装程序，用户看不到文件夹里
有多少东西 —— onefile 那点「只有一个文件」的好处在这里换不到任何东西。
"""
from pathlib import Path

ROOT = Path(SPECPATH)

# 只带运行时真正用得上的：立绘、闭眼帧、音效、人设。
# assets/ 里的 _*.png 是目检图、taffy.ico 是给快捷方式和安装程序用的，进包纯白占体积。
DATAS = [
    (str(ROOT / "assets" / "taffy.png"), "assets"),
    (str(ROOT / "assets" / "taffy_blink.png"), "assets"),
    (str(ROOT / "assets" / "sounds" / "click.wav"), "assets/sounds"),
    (str(ROOT / "assets" / "persona.md"), "assets"),
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATAS,
    # QSoundEffect 是在函数里 import 的（pyqt 少装一个模块也别让整个程序起不来），
    # 静态分析其实找得到，这里是保险。
    hiddenimports=["PyQt5.QtMultimedia"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 这几个只有 tools/ 里的素材脚本用得到，桌宠本身一行都不碰
    excludes=["numpy", "scipy", "cv2", "PIL", "tkinter", "matplotlib"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TaffyPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 压 PyQt5 的 DLL 容易压坏，省这点体积不值得
    upx=False,
    # 桌宠不能挂个黑框。控制台没了之后 sys.stdout 是 None，
    # main.py 的 _quiet_broken_output() 会把坏掉的输出丢进 devnull。
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "taffy.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TaffyPet",
)
