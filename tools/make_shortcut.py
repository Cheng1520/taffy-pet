"""往桌面上放一个「塔菲」图标，双击就启动。

    python tools/make_shortcut.py

产出：
    assets/taffy.ico      快捷方式用的图标（她的头；整张立绘是竖图，直接压成
                          方形图标会糊成一团，所以只取头）
    桌面上的「塔菲.lnk」   双击启动，不弹黑框命令行

快捷方式指向 pythonw.exe 而不是 python.exe —— 后者会挂一个黑框控制台窗口在
桌面上，关掉它还会把塔菲一起带走。

为什么绕 PowerShell 一圈：Windows 的 .lnk 是 COM 对象，纯 Python 写不出来
（没装 pywin32，也不想为这一个功能多一个依赖）。脚本用 -EncodedCommand
传 base64，是为了绕开中文在「bash → powershell.exe」这一路上的编码问题 ——
直接 -Command 传中文会变乱码，快捷方式的名字就成了问号。
"""
import base64
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICON = ASSETS / "taffy.ico"
SHORTCUT_NAME = "塔菲"

HEAD_CROP = 0.46   # 头顶到下巴占整图高度的比例（对着图量出来的）
PAD = 0.08         # 图标四周留白，贴边会显得挤
ICON_PX = 256

PS_TEMPLATE = """$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut(
    (Join-Path $desktop '{name}.lnk'))
$lnk.TargetPath = '{target}'
$lnk.Arguments = '"{args}"'
$lnk.WorkingDirectory = '{cwd}'
$lnk.IconLocation = '{icon},0'
$lnk.Description = '{desc}'
$lnk.Save()
Write-Output $lnk.FullName
"""


def build_icon() -> Path:
    src = Image.open(ASSETS / "taffy.png").convert("RGBA")
    w, h = src.size
    # 她的头发左右铺满整幅图（呆毛和发辫都探到边），所以正方形的宽就是整幅图的宽
    side = min(w, round(h * HEAD_CROP))
    head = src.crop(((w - side) // 2, 0, (w + side) // 2, side))

    canvas_px = round(side / (1 - 2 * PAD))
    canvas = Image.new("RGBA", (canvas_px, canvas_px), (0, 0, 0, 0))
    canvas.paste(head, ((canvas_px - side) // 2, (canvas_px - side) // 2))
    master = canvas.resize((ICON_PX, ICON_PX), Image.LANCZOS)

    master.save(ICON, format="ICO", sizes=[
        (16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    master.save(ASSETS / "_icon_preview.png")   # 目检图，_*.png 已被 .gitignore 忽略
    return ICON


def powershell(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
        capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"PowerShell 失败：{(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def main() -> None:
    if not (ASSETS / "taffy.png").exists():
        raise SystemExit("assets/taffy.png 不存在，先跑 python tools/build_assets.py")

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        raise SystemExit(f"找不到 {pythonw}，做不出「双击不弹黑框」的快捷方式")

    print(f"图标：{build_icon().relative_to(ROOT)}")

    lnk = powershell(PS_TEMPLATE.format(
        name=SHORTCUT_NAME,
        target=pythonw,
        args=ROOT / "main.py",
        cwd=ROOT,
        icon=ICON,
        desc="塔菲桌宠 —— 双击启动，右键点她退出",
    ))
    print(f"快捷方式：{lnk}")


if __name__ == "__main__":
    main()
