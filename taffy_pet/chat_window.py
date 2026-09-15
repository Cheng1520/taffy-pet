"""聊天窗口。

用系统标准窗口，只给标题栏上色（Task 6），不做无边框 —— 无边框的话拖边缩放、
窗口吸附、最小化动画全得自己实现，换来的只是「标题栏颜色可控」，而 Windows 11
的 DwmSetWindowAttribute 本来就能做这件事。
"""
import shutil
import sys
from datetime import datetime

from PyQt5.QtCore import Qt, QRectF, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                             QMessageBox, QPushButton, QScrollArea,
                             QSizePolicy, QTextEdit, QVBoxLayout, QWidget)

from . import agent
from . import chat as chatmod
from . import chat_store
from . import config as cfgmod
from . import memory
from .paths import ASSETS, PERSONA_DEFAULT, PERSONA_PATH
# 配色只有这一个来源（计划的 Global Constraints）：气泡那四个常量直接引用 toast 的，
# QSS 里的字面量也从它们拼出来。以前这里是手抄的一份，抄漏了两处 —— 窗口底色和系统
# 提示文字跟 toast 已经对不上了，而「以后改桌宠配色聊天窗跟着变」这件事完全没保证。
# 注意 toast.BG 带 α244（气泡是半透明的），聊天窗不需要半透明底，所以 .name() 取的是
# 不带 α 的那个十六进制串；别用 HexArgb，那会把 244 一起带进来。
from .toast import BG, BORDER, TEXT, DIM

BUBBLE_R = 14          # 气泡圆角
TAIL_W = 10            # 尾巴根部宽
TAIL_H = 9             # 尾巴伸出高度
PAD_X = 13             # 气泡内边距
PAD_Y = 9
HERS_BG = QColor(255, 255, 255)        # 她的气泡是实心白，跟 toast 的半透明底不是一回事
HERS_LINE = BORDER                     # 气泡描边跟 toast 同一个来源
MINE_BG = QColor(228, 150, 175)        # 用户气泡的粉底，toast 里没有对应物
MINE_LINE = QColor(221, 134, 163)      # 它的描边
BAR_BG = QColor(255, 246, 242)         # 底下那条输入区的底色
BAR_LINE = QColor(246, 223, 230)       # 输入区上边线
BTN_BUSY = QColor(185, 174, 180)       # 忙时按钮（灰掉，它现在是「停止」）
BTN_BUSY_HOVER = QColor(169, 158, 164)

AVATAR_H = 56          # 头像立绘的高度；宽度按原图比例走，不固定

# 她还没吐第一个字时气泡里显示的东西。
#
# `_start_reply` 先把气泡建出来、首块到了才填字，中间这段时间（DeepSeek 通常 1~3 秒）
# 屏幕上是一个**空气泡** —— 看着像界面卡住了。给个占位。
# 别改成「正在输入…」那种长提示：气泡宽度是按内容撑的，一多一少会让整行跳一下。
TYPING = "…"

# 日期分隔线那个 QLabel 的 objectName。测试靠它把「日期分隔线」和「气泡/系统提示」
# 分开数（见 tools/test_chat_window.py 的 texts()/rows()），改这个名字要一起改。
DAY_OBJECT = "daySeparator"

# QSS 里的 {} 是它自己的语法，写在这个 f-string 里得翻倍
QSS = f"""
#chatRoot, QScrollArea, #chatArea {{ background: {BG.name()}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px 4px 0; }}
QScrollBar::handle:vertical {{
    background: rgba({BORDER.red()}, {BORDER.green()}, {BORDER.blue()}, 150);
    border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba({MINE_BG.red()}, {MINE_BG.green()}, {MINE_BG.blue()}, 210);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
#chatInput {{
    background: #FFFFFF; border: 1.4px solid {BORDER.name()}; border-radius: 12px;
    padding: 6px 10px; color: {TEXT.name()};
}}
#chatInput:focus {{ border: 1.4px solid {MINE_BG.name()}; }}
#sendBtn {{
    background: {MINE_BG.name()}; color: #FFFFFF; border: none; border-radius: 12px;
}}
#sendBtn:hover {{ background: {MINE_LINE.name()}; }}
#sendBtn[busy="true"] {{ background: {BTN_BUSY.name()}; }}
#sendBtn[busy="true"]:hover {{ background: {BTN_BUSY_HOVER.name()}; }}
#chatBar {{ background: {BAR_BG.name()}; border-top: 1px solid {BAR_LINE.name()}; }}
"""

_AVATAR = None         # 头像立绘的惰性缓存，见 _avatar_pixmap()


def _avatar_pixmap() -> QPixmap:
    """缩到 56 高、比例不变的立绘。模块级只算一次。

    回填 200 条历史时 `_append_widget` 要走 100 次，每次都从磁盘解码一张 369×800
    的图再缩放 —— 那是开窗时肉眼可见的卡顿，而这张图在整个进程里根本不会变。
    """
    global _AVATAR
    if _AVATAR is None:
        pm = QPixmap(str(ASSETS / "taffy.png"))
        _AVATAR = (pm.scaledToHeight(AVATAR_H, Qt.SmoothTransformation)
                   if not pm.isNull() else QPixmap())
    return _AVATAR


class ChatInput(QTextEdit):
    """回车发送，Shift+回车换行。QLineEdit 做不了换行，所以用 QTextEdit 自己接键。"""

    def __init__(self, on_send, parent=None):
        super().__init__(parent)
        self._on_send = on_send
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self._on_send()
            return
        super().keyPressEvent(e)


class _Bubble(QWidget):
    """一个气泡。圆角矩形加一条小尾巴，尾巴只给她的消息 —— 用户的消息靠右，
    右边贴边没有空间伸尾巴，而且有头像的一侧本来就需要这个锚点。

    自己画而不是用 QSS：QSS 画不出尾巴，而跟 toast.py 保持一致的那套画法
    这里是现成的。
    """

    def __init__(self, text: str, mine: bool, parent=None):
        super().__init__(parent)
        self.mine = mine
        self._label = QLabel(text, self)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._label.setFont(QFont("Microsoft YaHei UI", 10))
        # 她的气泡文字跟 toast 同一个来源（这条 inline stylesheet 独立于模块级 QSS，
        # 手抄一份的话「改桌宠配色聊天窗跟着变」就又断在这儿）。白字那半边没有对应物：
        # 粉底上的白字 toast 里不存在，写死。
        self._label.setStyleSheet(
            f"color: {'#FFFFFF' if mine else TEXT.name()}; background: transparent;")

        lay = QVBoxLayout(self)
        left = PAD_X if mine else PAD_X + TAIL_W
        lay.setContentsMargins(left, PAD_Y, PAD_X, PAD_Y)
        lay.addWidget(self._label)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

    def text(self) -> str:
        """沿用 QLabel 那套接口。`_on_chunk` 和 Task 5 的测试都按 `text()` 读气泡，
        换掉 `_PendingLabel` 之后这一层得留着，不然后面调用方全要改。
        """
        return self._label.text()

    def set_text(self, text: str) -> None:
        self._label.setText(text)
        self.updateGeometry()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x = 0 if self.mine else TAIL_W
        body = QRectF(x + 0.5, 0.5, self.width() - x - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(body, BUBBLE_R, BUBBLE_R)
        if not self.mine:
            tail = QPainterPath()
            y = min(PAD_Y + 10.0, body.height() / 2)
            tail.moveTo(body.left() - TAIL_H + 1, y)
            tail.lineTo(body.left() + 1, y - TAIL_W / 2)
            tail.lineTo(body.left() + 1, y + TAIL_W / 2)
            tail.closeSubpath()
            path = path.united(tail)
        p.setPen(QPen(MINE_LINE if self.mine else HERS_LINE, 1.2))
        p.setBrush(MINE_BG if self.mine else HERS_BG)
        p.drawPath(path)


def day_label(ts: str) -> str:
    r"""把一条消息的时间戳变成「今天 / 昨天 / 9月14日」；算不出来返回空串。

    chat.json 里本来就有 `ts`（`chat_store.make` 写的），但渲染时一直没用 ——
    聊得多了之后「这句是什么时候说的」完全无从判断。

    **解析失败必须返回空串而不是抛**：`ts` 是文件里的字段，用户手改过、
    或者从旧版本带过来的记录都可能没有它。为了一个日期分隔线让整个窗口打不开，
    不值得。
    """
    try:
        d = datetime.fromisoformat(ts).date()
    except (TypeError, ValueError):
        return ""
    today = datetime.now().date()
    delta = (today - d).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "昨天"
    if d.year == today.year:
        return f"{d.month}月{d.day}日"
    return f"{d.year}年{d.month}月{d.day}日"


def paint_titlebar(win, caption: str, text_color: str) -> bool:
    """把系统标题栏刷成她的粉色。

    只在 Windows 11 上有效。Win10 会返回错误码，忽略即可 —— 退回系统默认配色，
    程序照常跑。所以整段包在 try 里：这是纯装饰，任何情况下都不该让窗口起不来。

    返回值是这件事上**唯一**的信号：DwmGetWindowAttribute 对 35/36 返回
    E_INVALIDARG（实测），设完再读回来比对这条路走不通。所以每个调用的 HRESULT
    都得接住 —— 不接的话 DWM 明确拒绝时这一函数照样报成功，外面那层 try 只抓
    Python 异常、抓不到非零 HRESULT，结果是 `_titlebar_painted` 被置上（不再重试）
    而一行日志都没有。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        def colorref(s: str) -> int:
            r, g, b = (int(s[i:i + 2], 16) for i in (1, 3, 5))
            return r | (g << 8) | (b << 16)

        dwm = ctypes.windll.dwmapi
        hwnd = ctypes.c_void_p(int(win.winId()))
        for attr, val in ((35, colorref(caption)),      # DWMWA_CAPTION_COLOR
                          (36, colorref(text_color))):  # DWMWA_TEXT_COLOR
            c = ctypes.c_int(val)
            hr = dwm.DwmSetWindowAttribute(hwnd, ctypes.c_uint(attr),
                                           ctypes.byref(c), ctypes.sizeof(c))
            if hr != 0:
                print(f"[chat] 标题栏上色失败（不影响使用）："
                      f"attr={attr} HRESULT=0x{hr & 0xFFFFFFFF:08X}")
                return False
        return True
    except Exception as e:                              # noqa: BLE001
        print(f"[chat] 标题栏上色失败（不影响使用）：{e}")
        return False


class ChatWindow(QWidget):
    def __init__(self, cfg: dict, parent=None, voice=None):
        super().__init__(parent)
        self.cfg = cfg
        # 语音库由 Pet 持有（音量只有一份），这儿只拿来播。没有就是不出声，
        # 其余功能一律照常 —— 语音是附加值，不该成为依赖。
        self.voice = voice
        self.worker = None
        self.pending = None          # 正在流式填充的那个气泡
        self.pending_text = ""       # 这一轮已经吐出来的字，_on_chunk 往里攒
        self.history = chat_store.load()
        # 是不是「贴着底部」。新气泡长高之后要跟着滚，但用户自己往上翻的时候不能
        # 把他拽下来 —— 跟随/不打扰两件事都挂在这一个标志上，见 _on_range_changed。
        self._stick = True
        # 已经插过的那个日期分隔线的文字。跨天才插新的，不然每条消息前面都顶一个。
        self._last_day = ""
        # 本轮工具调用往返的记录和轮次（见 _launch / _on_tools）。_start_reply 会重置，
        # 这儿先建出来是为了「还没发过消息就 _drop_pending」之类的路径不会 AttributeError。
        self._agent_msgs = []
        self._round = 0

        # 退出程序时必须把线程收掉，而窗口未必收得到 closeEvent：右键点塔菲 →「退出」
        # 走的是 PetWindow.quit() → QApplication.quit()，聊天窗从没 close() 过。
        # 进程收尾时窗口会带着**还在跑**的子 QThread 一起析构，Qt 直接 abort
        # （0xC0000409，实测过；那不是 Python 异常，main.py 的 sys.excepthook 接不住，
        # pythonw 下用户只看到程序凭空消失）。
        # 挂在 aboutToQuit 上是因为它是所有退出路径的必经之处；_stop_worker 自己有
        # worker is None 的早退，被重复调到也没事。self 是 QObject，窗口销毁时这条
        # 连接会被 Qt 自动断开，不会留下悬垂回调。
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_worker)

        self.setWindowTitle("塔菲")
        self.setMinimumSize(320, 400)
        self._restore_geometry()

        self._build_ui()
        self._render_history()

    # ---------- 搭界面 ----------
    def _build_ui(self) -> None:
        self.setObjectName("chatRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.area = QWidget()
        self.area.setObjectName("chatArea")
        self.msgs = QVBoxLayout(self.area)
        self.msgs.setContentsMargins(14, 14, 14, 14)
        self.msgs.setSpacing(10)
        self.msgs.addStretch(1)                  # 消息从上面排起，这个弹簧永远在最末
        self.scroll.setWidget(self.area)
        root.addWidget(self.scroll, 1)

        # 跟随滚动挂在这儿，不是挂在 _on_chunk 里 —— 见 _on_range_changed 的注释
        vbar = self.scroll.verticalScrollBar()
        vbar.rangeChanged.connect(self._on_range_changed)
        vbar.valueChanged.connect(self._on_value_changed)

        bar = QWidget()
        bar.setObjectName("chatBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 10, 14, 12)
        row.setSpacing(8)
        self.input = ChatInput(self.send)
        self.input.setObjectName("chatInput")
        self.input.setPlaceholderText("和 taffy 说点什么…（回车发送，Shift+回车换行）")
        self.input.setFixedHeight(72)
        row.addWidget(self.input, 1)

        self.btn = QPushButton("发送")
        self.btn.setObjectName("sendBtn")
        self.btn.setFixedSize(72, 72)
        self.btn.clicked.connect(self._on_button)
        row.addWidget(self.btn)
        root.addWidget(bar)

        self.setStyleSheet(QSS)

    def _at_bottom(self) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_range_changed(self, _lo: int, hi: int) -> None:
        """滚动范围一变就贴到底 —— 只要还在跟随。

        必须在 rangeChanged 上做，不能像以前那样在 _on_chunk 里直接
        `setValue(maximum())`：新气泡长高之后 Qt 的重排是**延迟**的，同一轮事件处理
        里读到的 maximum() 还是旧值，那一滚等于没滚。更糟的是它自我击败 —— 滚完
        value 还是旧的、max 已经涨上去了，下一次 _at_bottom() 从此恒为 False，
        后面所有 chunk 再也不会滚（实测：12 块里 value 一动不动，差 172px）。
        """
        if self._stick:
            self.scroll.verticalScrollBar().setValue(hi)

    def _on_value_changed(self, _v: int) -> None:
        """用户自己往上翻就走开，滚回底部就恢复跟随。

        「不打扰往上翻」以前根本没实现：_on_chunk 里那个 stick 一旦失效就永远是
        False，而 _append_widget 又是无条件滚的。
        """
        self._stick = self._at_bottom()

    def _add(self, widget) -> None:
        # 插在弹簧前面，否则新消息会跑到下面去
        self.msgs.insertWidget(self.msgs.count() - 1, widget)

    def _apply_width_to(self, bubble) -> None:
        """只给这一个气泡设 75% 上限。

        窗口可以缩放，写死像素必然错，所以按当前视口宽实时算。加一条气泡就全量刷
        一遍的话是 O(n²)：回填 200 条历史时每条都要遍历一遍已经建出来的所有气泡，
        实测开窗要 2 秒白屏（原生 5 秒）。新加的那条只需要它自己这一次。
        """
        bubble.setMaximumWidth(int(self.scroll.viewport().width() * 0.75))

    def _apply_bubble_width(self) -> None:
        """全量刷一遍，只在 resizeEvent 里调 —— 窗口缩放之后所有气泡的上限都得跟着变。"""
        for b in self.area.findChildren(_Bubble):
            self._apply_width_to(b)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._apply_bubble_width()

    # ---------- 渲染 ----------
    def _render_history(self) -> None:
        self._last_day = ""
        for m in self.history:
            self._maybe_day(m.get("ts", ""))
            self._append_widget(m["content"], m["role"] == "user")
        if not self.history:
            self._append_notice("和 taffy 打个招呼吧喵")
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _maybe_day(self, ts: str) -> None:
        """跨天了就插一条日期分隔。同一天连着多少条都只插一次。"""
        lab = day_label(ts)
        if lab and lab != self._last_day:
            self._last_day = lab
            self._append_day(lab)

    def _append_day(self, text: str) -> None:
        lab = QLabel(text)
        # 打个标记。日期分隔线是消息区里**第三类**顶层控件（气泡、系统提示之外的），
        # 测试里数「行数/气泡数」的地方按老口径数的是前两类，没这个标记就得靠
        # 「今天」这两个字去认，改文案就崩。
        lab.setObjectName(DAY_OBJECT)
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet(f"color: {DIM.name()}; font-family: 'Microsoft YaHei UI';"
                          " font-size: 8.5pt; padding: 8px 0 2px 0;")
        self._add(lab)

    def _append_widget(self, text: str, mine: bool):
        """返回那个气泡控件；真正插进布局的是 `bubble._outer`。

        Task 5 里「返回的控件」和「插进布局的控件」是同一个。这一轮她那边多了一层
        装头像的行，两者不再相等 —— `_drop_pending` 靠 `_outer` 删对那一个，删错的
        话气泡没了、那一行还杵在消息区里。
        """
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        bubble = _Bubble(text, mine)
        if mine:
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            avatar = QLabel()
            avatar.setStyleSheet("background: transparent;")
            pm = _avatar_pixmap()
            if not pm.isNull():
                avatar.setPixmap(pm)
                # 跟着缩放后的立绘走，不写死 56。原图 369×800 缩到 56 高只有 26 宽，
                # 框成 56×56 的话右边会空出 30px，正好夹在头像和气泡中间把它们推远。
                avatar.setFixedSize(pm.width(), pm.height())
            row.addWidget(avatar, 0, Qt.AlignTop)
            row.addWidget(bubble)
            row.addStretch(1)
        bubble._outer = holder          # 中途要撤销的时候删的是这个，不是 bubble
        self._add(holder)
        # 新气泡也得吃到 75% 上限。只靠 resizeEvent 的话，窗口最后一次缩放之后
        # 产生的气泡拿的是默认上限，长消息会超出去。只刷这一个 —— 全量刷一遍是
        # O(n²)，见 _apply_width_to。
        self._apply_width_to(bubble)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return bubble

    def _append_notice(self, text: str) -> None:
        lab = QLabel(text)
        lab.setAlignment(Qt.AlignCenter)
        lab.setWordWrap(True)
        lab.setStyleSheet(f"color: {DIM.name()}; font-family: 'Microsoft YaHei UI';"
                          " font-size: 9.5pt; padding: 6px;")
        self._add(lab)

    # ---------- 发送 ----------
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.worker is not None:
            return
        self.input.clear()
        msg = chat_store.make("user", text)
        self.history.append(msg)
        # 开着的窗口跨过午夜时，新消息前面也得补一条日期 —— 只在 _render_history 里
        # 插的话，日期分隔会一直停在开窗那天的。
        self._maybe_day(msg["ts"])
        self._append_widget(text, True)
        chat_store.save(self.history)
        self._start_reply()

    def _start_reply(self) -> None:
        self.pending_text = ""
        # 占位符不是内容：`pending_text` 照旧从空开始攒，气泡里先显示个「…」，
        # 首块一到就被 _on_chunk 覆盖掉。
        self.pending = self._append_widget(TYPING, False)
        # 本轮的工具调用脚手架（她调工具的往返记录）。只在这一次回复内部流转，
        # 每一轮用户发言都从零开始 —— 不重置的话上一轮的工具往返会一直堆在
        # 请求里，越滚越大。
        self._agent_msgs = []
        self._round = 0
        self._launch()

    def _launch(self) -> None:
        """发一轮请求。工具往返时会被连着调好几次，每次都复用同一个占位气泡。"""
        # 最后一轮不带工具：她要是还想着调工具，这一轮就会被逼着把话说出来，
        # 而不是无限往返下去。这是 MAX_ROUNDS 之外的第二道闸。
        use_tools = (bool(self.cfg.get("agent", True))
                     and self._round < agent.MAX_ROUNDS)
        self.worker = chatmod.ChatWorker(
            cfgmod.api_key(self.cfg),
            chatmod.build_messages(chatmod.load_persona(), self.history,
                                   memory.as_prompt(), self._agent_msgs),
            self,
            tools=agent.TOOLS if use_tools else None)
        self.worker.chunk.connect(self._on_chunk)
        self.worker.done.connect(self._on_done)
        self.worker.fail.connect(self._on_fail)
        self.worker.tool_calls.connect(self._on_tools)
        self.worker.start()
        self._set_busy(True)

    def _on_tools(self, calls: list) -> None:
        """她要求调工具：执行 -> 把往返记进脚手架 -> 带着结果再问一轮。

        注意这条路上**不碰 `pending_text`**：工具往返期间气泡里一直显示「…」，
        因为她确实还没开始说话。
        """
        self._round += 1
        self._agent_msgs.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": c["id"] or f"call_{i}", "type": "function",
                            "function": {"name": c["name"],
                                         "arguments": c["arguments"]}}
                           for i, c in enumerate(calls)],
        })
        for i, c in enumerate(calls):
            result = agent.run(c["name"], agent.parse_args(c["arguments"]))
            self._agent_msgs.append({
                "role": "tool", "tool_call_id": c["id"] or f"call_{i}",
                "content": result,
            })
        # 她调工具那一轮可能先吐了句「让我想想…」再调，那些字已经流进 pending_text 和
        # 气泡了。不在这儿清掉的话，下一轮的字会**接在后面**，而 _on_done 最后是用
        # 完整文本覆盖气泡的 —— 屏幕上会先看到「让我想想…答」，然后跳成「答」。
        self.pending_text = ""
        if self.pending is not None:
            self.pending.set_text(TYPING)
        self.worker = None
        self._launch()

    def _on_chunk(self, piece: str) -> None:
        # 只管填字，滚动交给 _on_range_changed —— 气泡长高之后 rangeChanged 才带着
        # 新算出来的 maximum 过来，在这儿读到的永远是旧值（Critical 1）。
        self.pending_text += piece
        if self.pending is not None:
            self.pending.set_text(self.pending_text)

    def _on_done(self, full: str) -> None:
        self.worker = None
        self._set_busy(False)
        if not full.strip():
            self._drop_pending()                 # 用户点了停止，一个字都没吐出来
            return
        if self.pending is not None:
            self.pending.set_text(full)          # 用完整文本覆盖，跟落盘的一致
        self.history.append(chat_store.make("assistant", full))
        chat_store.save(self.history)
        self.pending = None
        # 出声放在最后：挑不出贴切的台词就什么都不播（见 voice.pick）。
        # 也**不给她「说话」的图形状态** —— 音画对不上时那比光出声更假。
        if self.voice is not None:
            self.voice.play(full)

    def _on_fail(self, msg: str) -> None:
        self.worker = None
        self._set_busy(False)
        # 断流时那个半句必须清掉：它从来没写进过 chat.json，留着的话下次开窗
        # _render_history 一跑它就凭空消失 —— 界面和落盘对不上，比干脆不显示更让人困惑。
        # 别「顺手」改成保留。
        self._drop_pending()
        self._append_notice(msg)

    def _drop_pending(self) -> None:
        # 攒的增量也一起清掉 —— 不清的话它会留到下一轮 _start_reply 之前，
        # 谁在这中间碰一下 self.pending_text 就会读到上一轮的残字。
        self.pending_text = ""
        self._agent_msgs = []          # 这一轮废了，工具往返的脚手架也一起扔掉
        if self.pending is not None:
            outer = getattr(self.pending, "_outer", self.pending)
            self.msgs.removeWidget(outer)
            outer.deleteLater()
            self.pending = None

    def _on_button(self) -> None:
        if self.worker is not None:
            self.worker.stop()
        else:
            self.send()

    def _set_busy(self, busy: bool) -> None:
        self.btn.setText("停止" if busy else "发送")
        # busy 是动态属性，QSS 不认属性变化，得手动重刷一遍才重算
        self.btn.setProperty("busy", "true" if busy else "false")
        self.btn.style().unpolish(self.btn)
        self.btn.style().polish(self.btn)

    # ---------- 生命周期 ----------
    def _stop_worker(self) -> None:
        if self.worker is None:
            return
        self.worker.stop()
        # 先断信号再等：万一 wait 超时，线程还活着也不能碰已经要销毁的控件。
        try:
            self.worker.disconnect()
        except TypeError:
            pass
        if not self.worker.wait(3000):
            # stop() 只是置个标志位，要等下一块数据到了才退出循环；模型那边一停顿
            # 超过 3 秒就会走到这儿，长回复里很常见。
            print("[chat] 线程停在读上没收住，强杀")
            # terminate() 是「可能把它变成卡住」而不是「一定能杀」：Windows 上它走
            # TerminateThread，线程正握着 GIL 的时候连主线程一起锁死也是可能的。
            # 所以下面那一步不是形式主义。
            self.worker.terminate()
            self.worker.wait(500)
            if self.worker.isRunning():
                # 没杀干净就绝不能放手。线程还活着而引用丢了，窗口析构时 Qt 照样 abort
                # （见 __init__ 里那段）。宁可把引用留着，等下一次收尾
                # （关窗 / aboutToQuit）再试一次。
                #
                # 这一支在 Windows 上**实际走不到**：TerminateThread 是硬杀，随后的
                # wait(500) 必定让 isRunning() 变 False（实测）。能走到它的是
                # test_chat_window.py 里那个 _WedgedWorker 桩子。写了不是多余的：
                # 「线程还活着而引用丢了」正是 C1 那条 abort 路，防御方向是对的，
                # 别因为「反正跑不到」就删掉。
                # 这一支**有意不调** _set_busy(False)：线程是真的卡住了，按钮停在
                # 「停止」是诚实的；显示「发送」的话用户点下去会被 send() 里
                # worker is not None 的守卫静默吞掉，界面反而在撒谎。别「顺手修掉」。
                print("[chat] 强杀没成功，线程还活着，先留着引用不析构")
                self._drop_pending()
                return
        self.worker = None
        # 半句气泡必须在这儿清掉，不能只靠 _on_fail 里那次。closeEvent（关窗）和
        # clear_history（清空记录）都走这条路，两条路都会把控件删掉：
        # 不把 self.pending 置 None，它就成了指向已删控件的悬垂引用，Task 6 一碰就崩；
        # 而那个半句从没写进 chat.json，留在界面上的话下次开窗 _render_history 一跑
        # 它就凭空消失 —— 界面和落盘对不上，比干脆清掉更让人困惑（和 _on_fail 一个道理）。
        self._drop_pending()

    def closeEvent(self, e) -> None:
        self._stop_worker()
        self._save_geometry()
        cfgmod.save(self.cfg)
        super().closeEvent(e)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not getattr(self, "_titlebar_painted", False):
            # 得等窗口真的建出来再调，winId() 之前拿到的可能不是最终的那个句柄
            self._titlebar_painted = paint_titlebar(self, "#FBE3EA", "#3A2E34")

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _restore_geometry(self) -> None:
        g = self.cfg.get("chat_geometry")
        if isinstance(g, (list, tuple)) and len(g) == 4:
            self.setGeometry(int(g[0]), int(g[1]), int(g[2]), int(g[3]))
        else:
            self.resize(420, 560)

    def _save_geometry(self) -> None:
        self.cfg["chat_geometry"] = [self.x(), self.y(), self.width(), self.height()]

    # ---------- 菜单动作 ----------
    def edit_persona(self) -> None:
        """把默认人设复制到用户目录再用系统默认程序打开。

        用户目录里那份优先（`paths.persona_path()` 每次重判），所以改完切回来
        下一句话就用上了 —— 不用重启。已经有一份就只打开、不覆盖，不然用户
        自己写的人设会被随包的那份盖掉。
        """
        try:
            if not PERSONA_PATH.exists():
                PERSONA_PATH.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(PERSONA_DEFAULT, PERSONA_PATH)
        except OSError as e:
            QMessageBox.warning(self, "编辑人设", f"复制人设失败：{e}")
            return
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(PERSONA_PATH)))

    def clear_history(self) -> None:
        if QMessageBox.question(
                self, "清空对话记录",
                "把和 taffy 的聊天记录全部删掉？删了就找不回来了。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._stop_worker()
        chat_store.clear()
        self.history = []
        while self.msgs.count() > 1:
            item = self.msgs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._append_notice("清空了喵，重新开始")
