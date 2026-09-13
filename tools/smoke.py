"""冒烟测试：起窗口 -> 截几张 -> 退出。不开真窗口盯着看也能验证渲染对不对。

    python tools/smoke.py

另外算一遍弹跳过程中角色的最大外扩，确认不会被窗口边裁掉（呆毛最容易中招）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from taffy_pet import config as cfgmod  # noqa: E402
from taffy_pet import anim as animmod  # noqa: E402
from taffy_pet.pet import MARGIN, PetWindow  # noqa: E402


def check_bounds(pet) -> bool:
    """走完整条弹跳曲线，看缩放后的画面会不会超出窗口，顺带验幅度够不够大。

    解析地算，不依赖渲染 —— 截图看不出「小了一点」是设计还是 bug。
    """
    pw, ph = pet.pix.width(), pet.pix.height()
    win_w, win_h = pet.width(), pet.height()
    ok = True

    worst = {"left": float("inf"), "right": float("-inf"),
             "top": float("inf"), "bottom": float("-inf")}
    lo_sy, hi_sy = 1.0, 1.0
    for sx, sy, dy in animmod.bounce_curve():
        # 锚点是底部中心；加上呼吸的最坏偏移
        sy += animmod.BREATH_AMP
        half = pw / 2.0 * sx
        worst["left"] = min(worst["left"], MARGIN + pw / 2.0 - half)
        worst["right"] = max(worst["right"], MARGIN + pw / 2.0 + half)
        worst["top"] = min(worst["top"], MARGIN + ph - ph * sy + dy)
        worst["bottom"] = max(worst["bottom"], MARGIN + ph + dy)
        lo_sy, hi_sy = min(lo_sy, sy), max(hi_sy, sy)

    if worst["left"] < 0 or worst["right"] > win_w:
        print(f"  ✗ 横向溢出：{worst['left']:.1f} .. {worst['right']:.1f}（窗口宽 {win_w}）")
        ok = False
    if worst["top"] < 0 or worst["bottom"] > win_h:
        print(f"  ✗ 纵向溢出：{worst['top']:.1f} .. {worst['bottom']:.1f}（窗口高 {win_h}）")
        ok = False
    if ok:
        print(f"  ✓ 边界安全：x {worst['left']:.1f}..{worst['right']:.1f} / "
              f"y {worst['top']:.1f}..{worst['bottom']:.1f}"
              f"（窗口 {win_w}x{win_h}，边距 {MARGIN}）")

    # 幅度太小就等于没动画。压扁 5% 起步，肉眼才看得出。
    squash = 1.0 - lo_sy
    print(f"  纵向缩放范围 {lo_sy:.3f}..{hi_sy:.3f}"
          f"（压扁 {squash*100:.1f}%，拉伸 {(hi_sy-1)*100:.1f}%）")
    if squash < 0.05:
        print("  ✗ 压扁幅度不足 5%，等于没动 —— 调大 BOUNCE_KEYS 里 0.13 那帧")
        ok = False
    return ok


def main() -> int:
    out = ROOT / "assets" / "_smoke.png"
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv[:1])
    cfg = cfgmod.load()
    pet = PetWindow(cfg)
    pet.move(80, 80)
    pet.show()

    print("边界检查：")
    bounds_ok = check_bounds(pet)

    print("菜单检查：")
    try:
        m = pet.build_menu()
        labels = [a.text() for a in m.actions() if a.text()]
        print(f"  ✓ {len(labels)} 项：{' / '.join(labels)}")
    except Exception as e:                      # noqa: BLE001 - 冒烟测试就是要抓所有异常
        print(f"  ✗ 菜单构建失败：{type(e).__name__}: {e}")
        bounds_ok = False

    def shot_open():
        pet.grab().save(str(out))
        pet.animator.pounce()
        # 压扁峰值在进度 0.13，拉伸峰值在 0.30
        QTimer.singleShot(int(0.13 * animmod.BOUNCE_MS), shot_squash)

    def shot_squash():
        """弹跳最压扁的那一帧 —— 幅度调小了在这里看得出来。"""
        pet.grab().save(str(out.with_name("_smoke_squash.png")))
        QTimer.singleShot(int((0.30 - 0.13) * animmod.BOUNCE_MS), shot_stretch)

    def shot_stretch():
        pet.grab().save(str(out.with_name("_smoke_stretch.png")))
        pet.animator.set_blink_enabled(True)
        pet.animator._start_blink()
        QTimer.singleShot(40, shot_blink)

    def shot_blink():
        pet.grab().save(str(out.with_name("_smoke_blink.png")))
        pet.on_click()
        QTimer.singleShot(400, shot_toast)

    def shot_toast():
        pet.toast.grab().save(str(out.with_name("_smoke_toast.png")))
        print(f"截图已保存到 {out.parent}/_smoke*.png")
        app.quit()

    QTimer.singleShot(200, shot_open)
    app.exec_()
    return 0 if bounds_ok else 1


if __name__ == "__main__":
    sys.exit(main())
