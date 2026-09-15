"""冒烟测试：起窗口 -> 截几张 -> 退出。不开真窗口盯着看也能验证渲染对不对。

    python tools/smoke.py

另外算一遍弹跳过程中角色的最大外扩，确认不会被窗口边裁掉（呆毛最容易中招）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 控制台是 GBK 时，下面那些 ✓ 会直接把脚本打挂（UnicodeEncodeError 不是警告，
# 是崩）。把这个进程的 stdout 掰成 UTF-8，别要求用户记得加 PYTHONUTF8=1。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from taffy_pet import config as cfgmod  # noqa: E402
from taffy_pet import anim as animmod  # noqa: E402
from taffy_pet.pet import PetWindow  # noqa: E402


def check_bounds(pet) -> bool:
    """走完整条弹跳曲线，看缩放后的画面会不会超出窗口，顺带验幅度够不够大。

    解析地算，不依赖渲染 —— 截图看不出「小了一点」是设计还是 bug。
    """
    # 用实际绘制尺寸而不是资源像素 —— 资源是高分辨率的，和屏幕上的大小不是一回事。
    # 立绘画的是 pet.sprite_ar 那个宽度，不是窗口宽（窗口按最宽素材定，见 pet.py）。
    pw, ph = pet.sprite_ar * pet.disp_h, pet.disp_h
    win_w, win_h = pet.width(), pet.height()
    M = pet.margin
    # 每种素材都是水平居中画的（pet.py 的 left），所以中心永远是窗口中线
    cx = win_w / 2.0
    ok = True

    worst = {"left": float("inf"), "right": float("-inf"),
             "top": float("inf"), "bottom": float("-inf")}
    lo_sy, hi_sy = 1.0, 1.0
    for sx, sy, dy in animmod.bounce_curve():
        # 锚点是底部中心；加上呼吸的最坏偏移
        sy += animmod.BREATH_AMP
        half = pw / 2.0 * sx
        worst["left"] = min(worst["left"], cx - half)
        worst["right"] = max(worst["right"], cx + half)
        worst["top"] = min(worst["top"], M + ph - ph * sy + dy * ph)
        worst["bottom"] = max(worst["bottom"], M + ph + dy * ph)
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
              f"（窗口 {win_w}x{win_h}，边距 {M:.1f}）")

    # 幅度太小就等于没动画。压扁 5% 起步，肉眼才看得出。
    squash = 1.0 - lo_sy
    print(f"  纵向缩放范围 {lo_sy:.3f}..{hi_sy:.3f}"
          f"（压扁 {squash*100:.1f}%，拉伸 {(hi_sy-1)*100:.1f}%）")
    if squash < 0.05:
        print("  ✗ 压扁幅度不足 5%，等于没动 —— 调大 BOUNCE_KEYS 里 0.13 那帧")
        ok = False

    # 跳舞帧是横着最宽的一种素材，它就是窗口宽度该定多宽的依据。
    # 这条断言守的是「她一抬手，手被窗口切掉」—— 那正是把窗口宽度改成
    # 取最宽素材的直接原因，得有个东西盯着它别退化回去。
    if pet.pix_dance is None:
        print("  - 没装舞蹈素材，跳过跳舞取景检查")
    else:
        dw = pet.dance_ar * pet.disp_h
        if dw > win_w:
            print(f"  ✗ 跳舞帧宽 {dw:.1f} 超出窗口 {win_w}，她会缺手")
            ok = False
        else:
            print(f"  ✓ 跳舞取景：单帧 {dw:.1f}x{pet.disp_h:.1f}"
                  f"（窗口宽 {win_w}，还剩 {(win_w - dw) / 2:.1f}px 边距）")
    return ok


def check_voice(pet) -> None:
    r"""语音库能不能真加载 —— **这块 test_units 故意不测**。

    `_preload()` 要真建 QSoundEffect，而单元测试里没有 QApplication。
    可「QSoundEffect 建不起来」恰恰是最可能出问题的一步（QtMultimedia 缺后端、
    wav 格式不认），所以放到冒烟测试里过一遍真 Qt。

    没有语音库**不算失败** —— 语音是附加值，仓库里本来就不带音频。
    """
    v = pet.voice
    if not v.entries:
        print(r"  - 没装语音库（%APPDATA%\TaffyPet\voice\），跳过")
        return
    print(f"  ✓ 索引 {len(v.entries)} 条，建起 {len(v._effects)} 个播放器")
    if len(v._effects) != len(v.entries):
        print("  ✗ 有条目没能建成播放器（wav 格式不认？）")
    hit = v.pick("别熬夜了，早点睡")
    print(f"  ✓ 「别熬夜了」-> {hit['file'] if hit else '没挑到'}")
    print(f"  ✓ 无关的话 -> {v.pick('今天天气不错') or '没挑到（对）'}")


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

    print("语音检查：")
    check_voice(pet)

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
        if pet.pix_dance is None:
            print(f"截图已保存到 {out.parent}/_smoke*.png")
            app.quit()
            return
        pet.do_dance()
        # 取中段那一帧 —— 头几帧的站姿跟立绘差不多，看截图分不出画的是不是
        # 精灵表里的东西，那这张截图就白拍了。
        mid = pet.dance_meta["frames"] * 5 / 2 / float(pet.dance_meta.get("fps", 15.0))
        QTimer.singleShot(int(mid * 1000), shot_dance)

    def shot_dance():
        pet.grab().save(str(out.with_name("_smoke_dance.png")))
        print(f"  ✓ 跳舞第 {pet.animator.frame_index()} 帧已截图")
        print(f"截图已保存到 {out.parent}/_smoke*.png")
        app.quit()

    QTimer.singleShot(200, shot_open)
    app.exec_()
    return 0 if bounds_ok else 1


if __name__ == "__main__":
    sys.exit(main())
