"""闭眼帧探索 v6：OpenCV TELEA inpaint + 画睫毛弧。

失败史（改判据前务必先读）：
  v1  高斯模糊糊掉睫毛线；圆角半径取 min(w,h)//2 -> 覆盖到眉毛和脸颊
  v2  平涂肤色 -> 矩形补丁肉眼可见；左眼框采到腮红(250,202,194)涂出粉块
  v3  压扁原作眼睛 -> 虹膜被压成橙色横条。真实闭眼看不到虹膜，路线本身错了
  v4  以虹膜为中心的对称椭圆 -> 虹膜在眼中偏右，椭圆必然偏心，漏掉上睫毛
  v5  最近邻扩散修补 -> 掩膜边缘参差留下白毛边，且填充产生放射状拖影
      （用 _eye_ruler.py 量出：左眼实际 x[72..160] y[182..250]，虹膜只占 x[109..142]；
        扫描确认本图没有独立眉毛，182-193 那条深色带就是眼睛自己的上睫毛）

v6：掩膜多膨胀盖住抗锯齿过渡带，填充改用 cv2.inpaint(TELEA) —— 它就是干这个的。
"""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

img = Image.open(ASSETS / "taffy.png").convert("RGBA")
arr0 = np.asarray(img).astype(np.int32)
H, W = arr0.shape[:2]
r, g, b, alpha = arr0[..., 0], arr0[..., 1], arr0[..., 2], arr0[..., 3]


def iris_boxes():
    amber = (alpha > 128) & (r > 150) & (r - g > 25) & (g - b > 25) & (r - b > 60)
    m = np.zeros_like(amber)
    m[int(H * 0.18):int(H * 0.40), int(W * 0.10):int(W * 0.90)] = True
    lab, n = ndimage.label(amber & m)
    sizes = ndimage.sum(amber & m, lab, range(1, n + 1))
    out = []
    for i in np.argsort(sizes)[::-1][:2]:
        ys, xs = np.nonzero(lab == i + 1)
        out.append((int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())))
    out.sort(key=lambda t: t[0])
    return out


IRIS = iris_boxes()
mx = np.maximum(np.maximum(r, g), b)
mn = np.minimum(np.minimum(r, g), b)
chroma = mx - mn
SKIN = (r >= g) & (g - b >= 6) & (mx > 175) & (chroma < 60) & (alpha > 128)

# 粉发也是高彩度，光靠「非肤色」会把它算进眼睛掩膜，inpaint 出一块灰污渍。
# 粉发 g≈b（同色系），琥珀虹膜 g≫b，用这一点区分；再要求够亮以排除深棕眼线。
HAIR = (r > 180) & (r - g > 18) & (np.abs(g - b) < 30) & (chroma > 45)


def eye_mask(ir, dilate=6, guard=(1.65, 1.55)):
    """含虹膜的非肤色连通块，膨胀盖住抗锯齿边，再限制在椭圆护栏内防止吃到头发。"""
    x0, y0, x1, y1 = ir
    icx, icy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    iw, ih = x1 - x0 + 1, y1 - y0 + 1
    ex, ey = iw * guard[0], ih * guard[1]

    bx0, by0 = max(0, int(icx - ex) - 6), max(0, int(icy - ey) - 6)
    bx1, by1 = min(W, int(icx + ex) + 6), min(H, int(icy + ey) + 6)

    sub_not_skin = (~SKIN & ~HAIR)[by0:by1, bx0:bx1]
    sub_not_skin = ndimage.binary_closing(sub_not_skin, np.ones((3, 3)))
    lab, n = ndimage.label(sub_not_skin)
    ids = np.unique(lab[y0 - by0:y1 - by0 + 1, x0 - bx0:x1 - bx0 + 1])
    ids = ids[ids > 0]
    m = np.isin(lab, ids) if len(ids) else sub_not_skin
    m = ndimage.binary_fill_holes(m)
    m = ndimage.binary_dilation(m, np.ones((3, 3)), iterations=dilate)

    yy, xx = np.mgrid[by0:by1, bx0:bx1]
    guard_m = ((xx - icx) / ex) ** 2 + ((yy - icy) / ey) ** 2 <= 1.0
    m &= guard_m
    ys, xs = np.nonzero(m)
    bbox = (int(xs.min()) + bx0, int(ys.min()) + by0, int(xs.max()) + bx0, int(ys.max()) + by0)
    return m, (bx0, by0), bbox


def lash_color(bbox):
    x0, y0, x1, y1 = bbox
    band = arr0[y0:y0 + max(6, (y1 - y0) // 5), x0:x1]
    m = band[..., 3] > 128
    if not m.any():
        return (60, 40, 45)
    px = band[m][:, :3]
    dark = px[px.sum(axis=1).argsort()[: max(1, len(px) // 10)]]
    return tuple(float(v) for v in dark.mean(axis=0))


def inpaint_box(out, m, off, snap=0.35):
    """cv2.inpaint 填 RGB，alpha 置满。

    掩膜上边界常紧贴深色发丝，inpaint 会把发色抹进填充区，形成灰污渍。
    收尾只把「明显偏暗」的像素朝肤色基准拉回 —— 整块压缩会把区域压平、
    露出矩形补丁（试过，退回 v2 的错误），所以必须做得很局部。
    """
    bx0, by0 = off
    h, w = m.shape
    sub = out[by0:by0 + h, bx0:bx0 + w]
    bgr = sub[..., :3][..., ::-1].astype(np.uint8)
    filled = cv2.inpaint(bgr, (m * 255).astype(np.uint8), 5, cv2.INPAINT_TELEA)
    rgb = filled[..., ::-1].astype(np.float64)

    if snap > 0:
        ring = ndimage.binary_dilation(m, np.ones((3, 3)), iterations=6) & ~m
        src = arr0[by0:by0 + h, bx0:bx0 + w]
        ring_skin = ring & SKIN[by0:by0 + h, bx0:bx0 + w]
        use = ring_skin if ring_skin.sum() > 30 else ring
        if use.sum() > 10:
            ref = np.median(src[use][:, :3], axis=0)
            ref_luma = float(ref.mean())
            luma = rgb.mean(axis=2)
            # 只在「比肤色基准暗一大截」的地方生效，正常填充不动
            t = np.clip((ref_luma * 0.94 - luma) / (ref_luma * 0.35), 0.0, 1.0) * snap
            rgb = rgb * (1 - t[..., None]) + ref * t[..., None]
    sub[..., :3] = np.clip(rgb, 0, 255)
    sub[m, 3] = 255.0


def apply(arch=0.18, thick_f=0.16, y_at=0.58, x_in=0.04, dilate=6, snap=0.35,
          show_mask=False):
    out = np.asarray(img).astype(np.float64).copy()
    for ir in IRIS:
        m, off, bbox = eye_mask(ir, dilate=dilate)
        ex0, ey0, ex1, ey1 = bbox
        ew, eh = ex1 - ex0, ey1 - ey0

        if show_mask:
            sub = out[off[1]:off[1] + m.shape[0], off[0]:off[0] + m.shape[1]]
            sub[m] = [255, 0, 255, 255]
            continue

        inpaint_box(out, m, off, snap=snap)

        lash = lash_color(bbox)
        y_base = ey0 + eh * y_at
        x_a, x_b = ex0 + ew * x_in, ex1 - ew * x_in
        cx = (x_a + x_b) / 2
        y_ctrl = y_base - eh * arch
        pts = [((1 - t) ** 2 * x_a + 2 * (1 - t) * t * cx + t ** 2 * x_b,
                (1 - t) ** 2 * y_base + 2 * (1 - t) * t * y_ctrl + t ** 2 * y_base)
               for t in (i / 80 for i in range(81))]

        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        base_w = max(2.0, eh * thick_f)
        for i in range(len(pts) - 1):
            t = (i + 0.5) / (len(pts) - 1)
            taper = 1.0 - 0.62 * (abs(t - 0.5) * 2) ** 2.6
            d.line([pts[i], pts[i + 1]], fill=tuple(int(v) for v in lash) + (255,),
                   width=max(1, int(round(base_w * taper))))
        out = np.asarray(Image.alpha_composite(
            Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA"), layer)).astype(np.float64)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


for ir in IRIS:
    _, _, bb = eye_mask(ir)
    print(f"  虹膜 {ir} -> 掩膜边界 x[{bb[0]}..{bb[2]}] y[{bb[1]}..{bb[3]}] ({bb[2]-bb[0]+1}x{bb[3]-bb[1]+1})")

variants = [
    ("A_原图", img),
    ("B_snap0", apply(snap=0.0)),
    ("C_snap.25", apply(snap=0.25)),
    ("D_snap.45", apply(snap=0.45)),
    ("E_snap.70", apply(snap=0.70)),
    ("F_snap1.0", apply(snap=1.0)),
]

y0, y1 = int(H * 0.16), int(H * 0.42)
pad = 8
sheet = Image.new("RGB", (W + pad * 2, (y1 - y0) * len(variants) + pad * (len(variants) + 1)),
                  (245, 245, 248))
y = pad
for name, v in variants:
    face = v.crop((0, y0, W, y1))
    chk = Image.new("RGB", face.size, (255, 255, 255))
    chk.paste(face, (0, 0), face)
    sheet.paste(chk, (pad, y))
    y += chk.height + pad
sheet.resize((sheet.width * 2, sheet.height * 2), Image.LANCZOS).save(ASSETS / "_blink_variants.png")
print("已保存 _blink_variants.png  顺序:", " / ".join(n for n, _ in variants))
