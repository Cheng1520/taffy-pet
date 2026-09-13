"""从原始立绘构建桌宠素材。

用法：
    python tools/build_assets.py <原图路径>

例：
    python tools/build_assets.py "C:/Users/me/Pictures/taffy_立绘.jpg"

产出：
    assets/taffy.png        抠好的立绘（透明背景）
    assets/taffy_blink.png  闭眼帧（眨眼用）
    assets/eyes.json        眼睛区域坐标（眨眼动画用）

原图（白底单张立绘）不跟着仓库走 —— 那是别人画的，我从能改的地方传进来。
所以这里是参数而不是写死的常量：写死的话别人 clone 下来必定打不开。

抠图判据说明（踩过坑之后定下来的）：
    不能用「到白色的距离」当判据 —— 浅粉头发 (251,183,184) 到白色的距离 ≈ 101，
    和 JPEG 灰噪声（d 可到 ~90）数值重叠，调容差必然二选一：
    要么留下灰噪声，要么把呆毛和发辫整个吃掉（实测 tol=100 吃掉 15% 浅粉像素）。
    改用「彩度 chroma = max-min」区分：灰噪声彩度≈0-5，粉发彩度=68，彻底分开。

闭眼帧的生成见 tools/_blink_explore.py 顶部的失败记录，那里写了六版走过的弯路。
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# --- 抠图参数（已通过探针验证）---
INSET = 8         # 裁掉外圈深灰边框
CHROMA_MAX = 14   # 彩度低于此值 = 无彩色（白底/灰噪声/投影）
BRIGHT_MIN = 170  # 亮度高于此值 = 亮（投影灰 193 会被清掉）

# 素材分辨率按「默认显示高度 × 4」存：
#   2 倍是给高 DPI 屏的（200% 缩放下 1 逻辑像素 = 2 物理像素），
#   再留一倍余量，这样用户把 config.json 的 height 调大也不会糊。
# 桌宠实际画多大由 pet.py 按 height 缩放，跟这里的像素数没关系。
DISPLAY_HEIGHT = 200
ASSET_SCALE = 4

# --- 闭眼帧参数 ---
LASH_THICK = 0.16    # 睫毛线宽 / 眼高
LASH_ARCH = 0.18     # 弧线拱高 / 眼高
LASH_Y_AT = 0.58     # 闭眼线在眼框内的垂直位置


# ============================ 抠图 ============================
def build_cutout(src: Path) -> Image.Image:
    im = Image.open(src).convert("RGB")
    w, h = im.size
    im = im.crop((INSET, INSET, w - INSET, h - INSET))
    a = np.asarray(im).astype(np.int32)

    mx, mn = a.max(axis=2), a.min(axis=2)
    cand = ((mx - mn) < CHROMA_MAX) & (mx > BRIGHT_MIN)   # 背景候选：无彩色 且 亮

    lab, _ = ndimage.label(cand)
    border = np.unique(np.concatenate([lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]]))
    border = border[border != 0]
    bg = np.isin(lab, border)

    fore = ndimage.binary_fill_holes(~bg)
    fore = ndimage.binary_opening(fore, np.ones((3, 3)))

    l2, n2 = ndimage.label(fore)
    sizes = ndimage.sum(fore, l2, range(1, n2 + 1))
    if n2 > 1:
        print(f"  前景连通域 {n2} 个，保留最大块，丢弃 {n2 - 1} 个碎片")
    fore = np.isin(l2, int(np.argmax(sizes)) + 1)

    holes = int((ndimage.binary_fill_holes(fore) & ~fore).sum())
    print(f"  内部空洞 = {holes} px（应为 0）")
    assert holes == 0, "轮廓内部出现透明像素，抠图有漏"

    rgba = np.dstack([np.asarray(im).astype(np.uint8),
                      np.where(fore, 255, 0).astype(np.uint8)])
    ys, xs = np.nonzero(fore)
    return Image.fromarray(rgba, "RGBA").crop(
        (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))


# ============================ 眼睛定位 ============================
def find_irises(img: Image.Image):
    """靠虹膜的琥珀橙色定位两眼。比硬编码坐标稳，换图也能用。"""
    a = np.asarray(img).astype(np.int32)
    H, W = a.shape[:2]
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    amber = (al > 128) & (r > 150) & (r - g > 25) & (g - b > 25) & (r - b > 60)
    box = np.zeros_like(amber)
    box[int(H * 0.18):int(H * 0.40), int(W * 0.10):int(W * 0.90)] = True
    lab, n = ndimage.label(amber & box)
    if n == 0:
        raise RuntimeError("没找到虹膜，色相判据要调")
    sizes = ndimage.sum(amber & box, lab, range(1, n + 1))
    out = []
    for i in np.argsort(sizes)[::-1][:2]:
        ys, xs = np.nonzero(lab == i + 1)
        out.append((int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())))
    out.sort(key=lambda t: t[0])
    if len(out) < 2:
        raise RuntimeError(f"只找到 {len(out)} 只眼睛")
    return out


def eye_boxes(irises, size):
    """虹膜框 -> 整眼框。虹膜只占眼中下部，上下都要外扩。"""
    W, H = size
    out = []
    for x0, y0, x1, y1 in irises:
        w, h = x1 - x0, y1 - y0
        out.append((max(0, int(x0 - w * 0.90)), max(0, int(y0 - h * 1.10)),
                    min(W, int(x1 + w * 0.90)), min(H, int(y1 + h * 0.80))))
    return out


# ============================ 闭眼帧 ============================
def _skin_hair(arr):
    r, g, b, al = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    skin = (r >= g) & (g - b >= 6) & (mx > 175) & ((mx - mn) < 60) & (al > 128)
    # 粉发也是高彩度，只靠「非肤色」会把它算进眼睛掩膜，inpaint 出灰污渍。
    # 粉发 g≈b（同色系），琥珀虹膜 g≫b，用这一点区分；再要求够亮以排除深棕眼线。
    hair = (r > 180) & (r - g > 18) & (np.abs(g - b) < 30) & ((mx - mn) > 45)
    return skin, skin | hair


def build_blink(img: Image.Image, irises, guard=(1.65, 1.75)) -> Image.Image:
    arr0 = np.asarray(img).astype(np.int32)
    H, W = arr0.shape[:2]
    skin, excluded = _skin_hair(arr0)
    out = np.asarray(img).astype(np.float64).copy()

    for ir in irises:
        x0, y0, x1, y1 = ir
        icx, icy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        iw, ih = x1 - x0 + 1, y1 - y0 + 1
        # 椭圆护栏，防止掩膜吃到头发。
        # 纵向 1.75 是量出来的：1.55 时右眼上方留着原图上睫毛的残影（显示尺寸下
        # 看得见一道深色痕），再往上扩到 1.95+ 就会啃掉刘海 —— 发丝轮廓是高对比的，
        # 缺一块比多一道睫毛线更扎眼。1.75 刚好盖掉残影又不碰刘海。
        ex, ey = iw * guard[0], ih * guard[1]

        bx0, by0 = max(0, int(icx - ex) - 6), max(0, int(icy - ey) - 6)
        bx1, by1 = min(W, int(icx + ex) + 6), min(H, int(icy + ey) + 6)

        cand = ~excluded[by0:by1, bx0:bx1]
        cand = ndimage.binary_closing(cand, np.ones((3, 3)))
        lab, _ = ndimage.label(cand)
        ids = np.unique(lab[y0 - by0:y1 - by0 + 1, x0 - bx0:x1 - bx0 + 1])
        ids = ids[ids > 0]
        m = np.isin(lab, ids) if len(ids) else cand
        m = ndimage.binary_fill_holes(m)
        m = ndimage.binary_dilation(m, np.ones((3, 3)), iterations=6)

        yy, xx = np.mgrid[by0:by1, bx0:bx1]
        m &= ((xx - icx) / ex) ** 2 + ((yy - icy) / ey) ** 2 <= 1.0

        ys, xs = np.nonzero(m)
        bbox = (int(xs.min()) + bx0, int(ys.min()) + by0,
                int(xs.max()) + bx0, int(ys.max()) + by0)
        ex0, ey0, ex1, ey1 = bbox
        ew, eh = ex1 - ex0, ey1 - ey0

        # 1) 填平眼睛区域
        sub = out[by0:by0 + m.shape[0], bx0:bx0 + m.shape[1]]
        hh, ww = m.shape

        # 逐行横向插值：只在同一行内、用被遮段左右两侧的像素做线性插值。
        #
        # 不用 cv2.inpaint 是因为它从上下左右各个方向取色，而眼睛上方紧贴着深色
        # 刘海 —— 发色被带下来，在眼睛上方抹出一块灰。试过的其它三条路：
        #   · 朝肤色基准拉回：能淡掉，但要压平区域，等于退回平涂补丁的老错误
        #   · 收紧掩膜护栏：更糟，护栏把掩膜上缘切出硬边，灰块反而变大
        #   · 按闭眼线上下镜像：左眼干净了，右眼那块灰仍在
        # 横向插值从根上避开发色：同一行左右两侧是刚出掩膜的干净皮肤，永远不往上取。
        rgb = sub[..., :3].astype(np.float64).copy()
        for y in range(hh):
            xs = np.nonzero(m[y])[0]
            if xs.size == 0:
                continue
            # 掩膜在一行里可能是几段，逐段填
            breaks = np.nonzero(np.diff(xs) > 1)[0]
            for a, b in zip(np.r_[xs[0], xs[breaks + 1]], np.r_[xs[breaks], xs[-1]]):
                left = rgb[y, a - 1] if a > 0 else None
                right = rgb[y, b + 1] if b + 1 < ww else None
                if left is None and right is None:
                    continue
                if left is None:
                    left = right
                if right is None:
                    right = left
                # 段内有 n = b-a+1 个像素，两端各留一格给左右取样点 -> 取 n 个内点
                t = np.linspace(0.0, 1.0, b - a + 3)[1:-1, None]
                rgb[y, a:b + 1] = left * (1 - t) + right * t

        # 逐行独立插值会让相邻行接不上，留下横向条带。沿纵向补一道高斯。
        # 必须用归一化卷积（只在掩膜内加权平均）——直接模糊的话，掩膜上缘会把
        # 上面的刘海又吸进来，等于绕一圈回到原来的灰渍问题。
        wm = m[..., None].astype(np.float64)
        den = ndimage.gaussian_filter1d(wm, 3.0, axis=0)
        num = ndimage.gaussian_filter1d(rgb * wm, 3.0, axis=0)
        rgb = np.where(m[..., None], num / np.maximum(den, 1e-6), rgb)

        sub[..., :3] = np.clip(rgb, 0, 255)
        sub[m, 3] = 255.0

        y_base = ey0 + eh * LASH_Y_AT

        # 2) 画睫毛弧。闭眼时上眼睑完全盖住虹膜，所以不能压扁原作眼睛
        #    （那样会把琥珀虹膜压成一条橙条），必须重画一道线。
        band = arr0[ey0:ey0 + max(6, eh // 5), ex0:ex1]
        sel_px = band[..., 3] > 128
        px = band[sel_px][:, :3]
        lash = tuple(float(v) for v in
                     px[px.sum(axis=1).argsort()[: max(1, len(px) // 10)]].mean(axis=0))

        x_a, x_b = ex0 + ew * 0.04, ex1 - ew * 0.04
        cx = (x_a + x_b) / 2
        y_ctrl = y_base - eh * LASH_ARCH
        pts = [((1 - t) ** 2 * x_a + 2 * (1 - t) * t * cx + t ** 2 * x_b,
                (1 - t) ** 2 * y_base + 2 * (1 - t) * t * y_ctrl + t ** 2 * y_base)
               for t in (i / 80 for i in range(81))]

        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        base_w = max(2.0, eh * LASH_THICK)
        for i in range(len(pts) - 1):
            t = (i + 0.5) / (len(pts) - 1)
            taper = 1.0 - 0.62 * (abs(t - 0.5) * 2) ** 2.6   # 两端收细成睫毛尖
            d.line([pts[i], pts[i + 1]], fill=tuple(int(v) for v in lash) + (255,),
                   width=max(1, int(round(base_w * taper))))
        out = np.asarray(Image.alpha_composite(
            Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA"),
            layer)).astype(np.float64)

    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


# ============================ 主流程 ============================
def main(src: Path) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    print(f"原图：{src}")
    print("抠图...")
    img = build_cutout(src)
    print(f"  角色包围盒 {img.width}x{img.height}  宽高比 {img.width/img.height:.3f}")

    th = DISPLAY_HEIGHT * ASSET_SCALE
    img = img.resize((round(img.width * th / img.height), th), Image.LANCZOS)
    img.save(ASSETS / "taffy.png")
    print(f"  已保存 assets/taffy.png  {img.size}")

    print("定位眼睛...")
    irises = find_irises(img)
    eyes = eye_boxes(irises, img.size)
    for name, b in zip(("left", "right"), eyes):
        print(f"  {name}: x[{b[0]}..{b[2]}] y[{b[1]}..{b[3]}] ({b[2]-b[0]+1}x{b[3]-b[1]+1})")
    (ASSETS / "eyes.json").write_text(
        json.dumps({n: {"x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]}
                    for n, b in zip(("left", "right"), eyes)}, indent=2), encoding="utf-8")

    print("生成闭眼帧...")
    build_blink(img, irises).save(ASSETS / "taffy_blink.png")
    print("  已保存 assets/taffy_blink.png")

    # 目检图：睁眼/闭眼并排，白底
    hi = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    hb = Image.open(ASSETS / "taffy_blink.png").resize(
        (img.width * 2, img.height * 2), Image.LANCZOS)
    y0, y1 = int(img.height * 0.16) * 2, int(img.height * 0.42) * 2
    panel = Image.new("RGB", ((hi.width) * 2 + 24, y1 - y0), (255, 255, 255))
    for i, src in enumerate((hi, hb)):
        face = src.crop((0, y0, src.width, y1))
        tmp = Image.new("RGB", face.size, (255, 255, 255))
        tmp.paste(face, (0, 0), face)
        panel.paste(tmp, (i * (hi.width + 24), 0))
    panel.save(ASSETS / "_blink_check.png")
    print("  已保存 assets/_blink_check.png（左=睁眼 右=闭眼）")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    src = Path(sys.argv[1])
    if not src.exists():
        sys.exit(f"找不到原图：{src}")
    main(src)
