"""给左眼区域套上 10px 格尺，肉眼量出眼睛真实上下边界（别猜）。探索用。"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

img = Image.open(ASSETS / "taffy.png").convert("RGBA")
arr = np.asarray(img).astype(np.int32)
H, W = arr.shape[:2]
r, g, b, alpha = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]

amber = (alpha > 128) & (r > 150) & (r - g > 25) & (g - b > 25) & (r - b > 60)
m = np.zeros_like(amber)
m[int(H * 0.18):int(H * 0.40), int(W * 0.10):int(W * 0.90)] = True
lab, n = ndimage.label(amber & m)
sizes = ndimage.sum(amber & m, lab, range(1, n + 1))
boxes = []
for i in np.argsort(sizes)[::-1][:2]:
    ys, xs = np.nonzero(lab == i + 1)
    boxes.append((int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())))
boxes.sort()

ir = boxes[0]  # 左眼
X0, Y0 = max(0, ir[0] - 55), max(0, ir[1] - 60)
X1, Y1 = min(W, ir[2] + 55), min(H, ir[3] + 55)
print(f"左眼虹膜 {ir}  裁剪区 x[{X0}..{X1}] y[{Y0}..{Y1}]")

Z = 6
crop = img.crop((X0, Y0, X1, Y1)).resize(((X1 - X0) * Z, (Y1 - Y0) * Z), Image.NEAREST)
bgc = Image.new("RGB", crop.size, (255, 255, 255))
bgc.paste(crop, (0, 0), crop)
d = ImageDraw.Draw(bgc)

for gx in range(X0 - X0 % 10 + 10, X1, 10):
    px = (gx - X0) * Z
    major = gx % 50 == 0
    d.line([(px, 0), (px, bgc.height)], fill=(0, 160, 255) if major else (170, 220, 255), width=2 if major else 1)
    if major:
        d.text((px + 3, 4), str(gx), fill=(0, 90, 200))
for gy in range(Y0 - Y0 % 10 + 10, Y1, 10):
    py = (gy - Y0) * Z
    major = gy % 50 == 0
    d.line([(0, py), (bgc.width, py)], fill=(0, 160, 255) if major else (170, 220, 255), width=2 if major else 1)
    if major:
        d.text((4, py + 3), str(gy), fill=(0, 90, 200))

# 把虹膜框标红
d.rectangle([(ir[0] - X0) * Z, (ir[1] - Y0) * Z, (ir[2] - X0) * Z, (ir[3] - Y0) * Z],
            outline=(255, 0, 0), width=3)
bgc.save(ASSETS / "_eye_ruler.png")
print("已保存 _eye_ruler.png")

# 顺带按列扫描：每列从上往下第一个「深色」像素的 y，用来判断上睫毛位置
dark = (alpha > 128) & (np.maximum(np.maximum(r, g), b) < 140)
print("\n列扫描：每列最上方深色像素的 y（判断上睫毛/眉毛边界）")
for gx in range(ir[0] - 30, ir[2] + 30, 8):
    col = np.nonzero(dark[Y0:Y1, gx])[0]
    if len(col):
        # 找所有深色段
        segs, s = [], col[0]
        for i in range(1, len(col)):
            if col[i] != col[i - 1] + 1:
                segs.append((s + Y0, col[i - 1] + Y0))
                s = col[i]
        segs.append((s + Y0, col[-1] + Y0))
        print(f"  x={gx:4d}  深色段(绝对y): {segs}")
    else:
        print(f"  x={gx:4d}  无深色")
