"""桌宠主窗口：无边框、透明、置顶，可拖动。

点击 = 说话 + 弹余额 + 播音效 + 弹一下。拖拽和点击要靠位移量区分 ——
否则想挪个位置就会触发一次说话。
"""
from pathlib import Path

from PyQt5.QtCore import Qt, QRectF, QTimer
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QApplication, QInputDialog, QLineEdit, QMenu, QWidget

from . import config as cfgmod
from .anim import PetAnimator
from .balance import BalanceFetcher
from .toast import Toast

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# 边距是角色显示高度的比例，不是固定像素 —— 角色的放大/弹跳都是按比例缩放的，
# 固定边距在角色调小之后会显得过大、调大之后又不够。
#
# 这个比例的来历：呼吸(+0.8%) 叠上弹跳拉伸(+3.0%) 是 3.8%，再加腾空（anim.py 的
# HOP，1.6%），合起来 5.4%，留到 6.5% 有余量。小了呆毛会在弹起那几帧被窗口顶边裁掉。
#
# 别再写「边距会挡住周围桌面图标」了 —— 量过，不会：窗口是带 alpha 的 layered window，
# 透明像素点得穿（WindowFromPoint 打在边距上返回的是下层窗口）。边距几乎不要钱。
# tools/smoke.py 会走完整条弹跳曲线来验这个余量够不够。
MARGIN_RATIO = 0.065
CLICK_SLOP = 6       # 位移小于这个像素数还算点击
CLICK_MS = 500       # 按下超过这么久算长按，不算点击


class PetWindow(QWidget):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool |
                            Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("塔菲")
        self.setMouseTracking(True)

        self.pix = self._load("taffy.png")
        self._blink_pix = self._load("taffy_blink.png")
        self.has_blink_asset = self._blink_pix is not None
        self.pix_blink = self._blink_pix or self.pix
        if self.pix is None:
            raise SystemExit("assets/taffy.png 不存在，先跑 python tools/build_assets.py")

        self.setWindowOpacity(float(cfg.get("opacity", 1.0)))

        # 目标显示高度是「逻辑像素」。资源本身是高分辨率（见 build_assets.py），
        # 这里按比例缩到 height 逻辑像素 —— 高 DPI 屏上会自动铺满对应的物理像素。
        #
        # 之前这里直接 1:1 把资源画出去，结果 200% 缩放的屏上角色占了整屏 95% 高度，
        # 头被顶到屏幕外，看着像「不在桌面上」。
        self.disp_h = max(40.0, float(cfg.get("height", 200)))
        self.disp_w = self.pix.width() * self.disp_h / self.pix.height()
        self.margin = max(6.0, self.disp_h * MARGIN_RATIO)
        self.resize(int(round(self.disp_w + self.margin * 2)),
                    int(round(self.disp_h + self.margin * 2)))

        self.toast = Toast()
        self.fetcher = None
        self._press = None
        self._press_t = None
        self._moved = False

        self.animator = PetAnimator(bool(cfg.get("blink", False)))
        self.animator.frame.connect(self.update)
        self.animator.start()

        self._sound = self._load_sound()
        self._restore_pos()

    # ---------- 载入 ----------
    def _load(self, name: str):
        p = ASSETS / name
        if not p.exists():
            return None
        pm = QPixmap(str(p))
        return pm if not pm.isNull() else None

    def _load_sound(self):
        p = ASSETS / "sounds" / "click.wav"
        if not p.exists():
            return None
        from PyQt5.QtMultimedia import QSoundEffect
        from PyQt5.QtCore import QUrl
        eff = QSoundEffect(self)
        eff.setSource(QUrl.fromLocalFile(str(p)))
        eff.setVolume(float(self.cfg.get("volume", 1.0)))
        return eff

    def play_sound(self) -> None:
        if self._sound is not None:
            self._sound.play()

    def _restore_pos(self) -> None:
        pos = self.cfg.get("pos")
        screen = QApplication.primaryScreen().availableGeometry()
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            x, y = self._clamp(int(pos[0]), int(pos[1]), screen)
            self.move(x, y)
            return
        # 默认落在右下角，但要夹在屏幕内 —— 算出来是负坐标时角色会被顶到屏幕外，
        # 看着就像「没启动」。
        self.move(*self._clamp(screen.right() - self.width() - 60,
                               screen.bottom() - self.height() - 10, screen))

    def _clamp(self, x: int, y: int, screen) -> tuple:
        """保证窗口至少有相当一部分留在屏幕内，别整只跑到屏幕外面去。"""
        x = max(screen.left() - self.width() // 3,
                min(x, screen.right() - self.width() * 2 // 3))
        y = max(screen.top() - self.height() // 3,
                min(y, screen.bottom() - self.height() * 2 // 3))
        return x, y

    # ---------- 绘制 ----------
    def paintEvent(self, _event) -> None:
        sx, sy, dy, blinking = self.animator.state()
        src = self.pix_blink if blinking else self.pix

        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        m = self.margin
        p.translate(w / 2.0, h - m)           # 锚点：底部中心
        p.scale(sx, sy)
        p.translate(-w / 2.0, -(h - m) + dy * self.disp_h)
        p.drawPixmap(QRectF(m, m, self.disp_w, self.disp_h), src, QRectF(src.rect()))

    # ---------- 交互 ----------
    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        self._press = e.globalPos()
        self._offset = e.globalPos() - self.frameGeometry().topLeft()
        self._press_t = QTimer()          # 只用来判断是不是长按
        self._press_t.start(CLICK_MS)
        self._moved = False

    def mouseMoveEvent(self, e) -> None:
        if self._press is None:
            return
        if (e.globalPos() - self._press).manhattanLength() > CLICK_SLOP:
            self._moved = True
            self.move(e.globalPos() - self._offset)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton or self._press is None:
            return
        long_press = self._press_t is not None and not self._press_t.isActive()
        was_click = (not self._moved) and (not long_press)
        self._press = None
        if self._press_t:
            self._press_t.stop()
            self._press_t = None
        if self._moved:
            self._remember_pos()
        if was_click:
            self.on_click()

    def show_hint_if_first_run(self) -> None:
        """透明窗口没有任何可见的边框，不提示的话没人知道能右键、能拖。"""
        if self.cfg.get("hint_shown"):
            return
        self.toast.show_message("拖我可以换位置 · 右键点我打开菜单")
        self.toast.anchor_above(self.frameGeometry())
        self.cfg["hint_shown"] = True
        cfgmod.save(self.cfg)

    def on_click(self) -> None:
        self.play_sound()
        self.animator.pounce()
        self.toast.show_message(self.cfg.get("speech", ""), "余额查询中…")
        self.toast.anchor_above(self.frameGeometry())
        self.refresh_balance()

    def build_menu(self) -> QMenu:
        """单独拆出来是为了能自动测 —— exec_() 会阻塞，没法在测试里直接调。"""
        m = QMenu(self)
        m.addAction("设置 API Key", self.ask_api_key)
        m.addAction("刷新余额", self.refresh_balance)

        blink = m.addAction("眨眼")
        blink.setCheckable(True)
        blink.setChecked(self.animator.blink_enabled)
        blink.setEnabled(self.has_blink_asset)
        if not self.has_blink_asset:
            blink.setText("眨眼（缺 taffy_blink.png）")
        blink.toggled.connect(self._set_blink)

        top = m.addAction("窗口置顶")
        top.setCheckable(True)
        top.setChecked(bool(self.cfg.get("always_on_top", True)))
        top.toggled.connect(self.set_always_on_top)

        m.addSeparator()
        m.addAction("退出", self.quit)
        return m

    def contextMenuEvent(self, e) -> None:
        self.build_menu().exec_(e.globalPos())

    # ---------- 菜单动作 ----------
    def _set_blink(self, on: bool) -> None:
        self.cfg["blink"] = bool(on)
        self.animator.set_blink_enabled(bool(on))
        cfgmod.save(self.cfg)

    def set_always_on_top(self, on: bool) -> None:
        self.cfg["always_on_top"] = bool(on)
        flags = self.windowFlags()
        self.setWindowFlags(flags | Qt.WindowStaysOnTopHint if on
                            else flags & ~Qt.WindowStaysOnTopHint)
        self.show()
        cfgmod.save(self.cfg)

    def ask_api_key(self) -> None:
        cur = self.cfg.get("api_key", "")
        shown = ("*" * len(cur[-6:]) + cur[-6:]) if len(cur) > 6 else cur
        text, ok = QInputDialog.getText(
            self, "设置 API Key",
            "粘贴 DeepSeek API Key（sk- 开头）：\n"
            f"当前来源：{cfgmod.api_key_source(self.cfg)}\n\n"
            "想用环境变量 DEEPSEEK_API_KEY 的话，把这里留空即可 —— 环境变量优先。",
            QLineEdit.Password, cur)
        if not ok:
            return
        self.cfg["api_key"] = text.strip()
        cfgmod.save(self.cfg)
        print(f"[apikey] 已保存（{shown or '空'} -> "
              f"{('*' * 6 + text.strip()[-4:]) if text.strip() else '空'}）")
        self.refresh_balance()

    # ---------- 余额 ----------
    def refresh_balance(self) -> None:
        if self.fetcher is not None and self.fetcher.isRunning():
            return
        key = cfgmod.api_key(self.cfg)
        if not key:
            self.toast.set_balance("未设置 API Key（右键设置）")
            return
        self.fetcher = BalanceFetcher(key, self)
        self.fetcher.ok.connect(lambda t: self.toast.set_balance(f"余额 {t}"))
        self.fetcher.fail.connect(lambda t: self.toast.set_balance(t))
        self.fetcher.start()

    # ---------- 收尾 ----------
    def _remember_pos(self) -> None:
        self.cfg["pos"] = [self.x(), self.y()]

    def quit(self) -> None:
        self._remember_pos()
        cfgmod.save(self.cfg)
        self.animator.stop()
        self.toast.hide()
        QApplication.quit()

    def closeEvent(self, e) -> None:
        self._remember_pos()
        cfgmod.save(self.cfg)
        self.animator.stop()
        self.toast.close()
        super().closeEvent(e)
