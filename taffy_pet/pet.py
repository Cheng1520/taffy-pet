"""桌宠主窗口：无边框、透明、置顶，可拖动。

点击 = 说话 + 弹余额 + 播音效 + 弹一下。拖拽和点击要靠位移量区分 ——
否则想挪个位置就会触发一次说话。
"""
from pathlib import Path

from PyQt5.QtCore import Qt, QPoint, QRectF, QTimer
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QApplication, QInputDialog, QLineEdit, QMenu, QWidget

from . import config as cfgmod
from .anim import PetAnimator
from .balance import BalanceFetcher
from .toast import Toast

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# 预留放大/弹跳的余量。呼吸(+0.8%)叠上弹跳拉伸(+3.0%)，760px 上最高长出约 29px，
# 再加腾空 12px，42 够用。小了呆毛会在弹起那几帧被窗口顶边裁掉；
# 但这个边距同时也是窗口挡桌面图标的面，所以别随手加大。
MARGIN = 42
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
        self.resize(self.pix.width() + MARGIN * 2, self.pix.height() + MARGIN * 2)

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
            x, y = int(pos[0]), int(pos[1])
            if screen.intersects(QRectF(x, y, self.width(), self.height()).toRect()):
                self.move(x, y)
                return
        self.move(screen.right() - self.width() - 60, screen.bottom() - self.height() - 10)

    # ---------- 绘制 ----------
    def paintEvent(self, _event) -> None:
        sx, sy, dy, blinking = self.animator.state()
        src = self.pix_blink if blinking else self.pix

        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        p.translate(w / 2.0, h - MARGIN)      # 锚点：底部中心
        p.scale(sx, sy)
        p.translate(-w / 2.0, -(h - MARGIN) + dy)
        p.drawPixmap(MARGIN, MARGIN, src)

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
