"""气泡：说话内容 + 余额，一条尾巴指回角色。

说话和余额共用一个窗口 —— 点击时两者同时出现，两个窗口要各自算位置、各自淡出，
还会互相盖住，得不偿失。
"""
from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QWidget

TAIL_W = 16       # 尾巴根部宽
TAIL_H = 12       # 尾巴伸出高度
PAD_X = 16
PAD_Y = 11
RADIUS = 12
GAP = 7           # 两行之间的间距
HOLD_MS = 3400    # 停留时长
FADE_MS = 260

BG = QColor(255, 253, 250, 244)
BORDER = QColor(240, 190, 205)
TEXT = QColor(58, 46, 52)
DIM = QColor(150, 120, 132)


class Toast(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool |
                            Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.speech = ""
        self.balance = ""
        self._opacity = 0.0
        self._text_font = QFont("Microsoft YaHei UI", 10)
        self._bal_font = QFont("Microsoft YaHei UI", 9)

        self._fade = QTimer(self)
        self._fade.setInterval(16)
        self._fade.timeout.connect(self._tick_fade)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._start_fade_out)

    # ---------- 对外 ----------
    def show_message(self, speech: str, balance: str = "") -> None:
        self.speech = speech or ""
        self.balance = balance or ""
        self._relayout()
        self._opacity = 1.0
        self.show()
        self.raise_()
        self.update()
        self._fade.stop()
        self._hold.start(HOLD_MS)

    def set_balance(self, balance: str) -> None:
        """只在气泡已经显示时更新余额（余额比说话慢，先出文字后补数字）。"""
        self.balance = balance or ""
        if self.isVisible():
            self._relayout()
            self.update()
            self._hold.start(HOLD_MS)

    def anchor_above(self, global_rect) -> None:
        """把尾巴尖对准角色头顶中央。"""
        x = global_rect.center().x() - self.width() // 2
        y = global_rect.top() - self.height()
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
            if y < screen.top() + 4:                       # 头顶放不下就挪到脚下
                y = global_rect.bottom()
        self.move(x, y)

    # ---------- 内部 ----------
    def _tick_fade(self) -> None:
        self._opacity = max(0.0, self._opacity - 16 / FADE_MS)
        if self._opacity <= 0.0:
            self._fade.stop()
            self.hide()
        self.update()

    def _start_fade_out(self) -> None:
        self._fade.start()

    def _relayout(self) -> None:
        fm = QFontMetrics(self._text_font)
        fmb = QFontMetrics(self._bal_font)
        w = fm.horizontalAdvance(self.speech) if self.speech else 0
        if self.balance:
            w = max(w, fmb.horizontalAdvance(self.balance))
        w = max(w, 60) + PAD_X * 2 + 8
        h = PAD_Y * 2 + TAIL_H
        if self.speech:
            h += fm.height()
        if self.balance:
            h += (GAP if self.speech else 0) + fmb.height()
        self.resize(int(w), int(h))

    def paintEvent(self, _event) -> None:
        if self._opacity <= 0.0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self._opacity)

        body = QRectF(0.5, 0.5, self.width() - 1, self.height() - TAIL_H - 1)
        path = QPainterPath()
        path.addRoundedRect(body, RADIUS, RADIUS)
        cx = self.width() / 2
        tail = QPainterPath()
        tail.moveTo(cx - TAIL_W / 2, body.bottom() - 1)
        tail.lineTo(cx, body.bottom() + TAIL_H - 1)
        tail.lineTo(cx + TAIL_W / 2, body.bottom() - 1)
        tail.closeSubpath()
        path = path.united(tail)

        p.setPen(QPen(BORDER, 1.4))
        p.setBrush(BG)
        p.drawPath(path)

        y = PAD_Y
        if self.speech:
            p.setFont(self._text_font)
            p.setPen(TEXT)
            fm = QFontMetrics(self._text_font)
            y += fm.ascent()
            p.drawText(QPointF(PAD_X + 4, y), self.speech)
            y += fm.descent()
        if self.balance:
            y += GAP
            fm = QFontMetrics(self._bal_font)
            p.setFont(self._bal_font)
            p.setPen(DIM)
            y += fm.ascent()
            p.drawText(QPointF(PAD_X + 4, y), self.balance)
