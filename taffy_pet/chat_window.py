"""聊天窗口。

用系统标准窗口，只给标题栏上色（Task 6），不做无边框 —— 无边框的话拖边缩放、
窗口吸附、最小化动画全得自己实现，换来的只是「标题栏颜色可控」，而 Windows 11
的 DwmSetWindowAttribute 本来就能做这件事。
"""
import shutil
import sys
from datetime import datetime

from PyQt5.QtCore import Qt, QRectF, QSize, QTimer
# QColor 这一版在模块里已经没人直接用了，但**不能删**：测试按 `CW.QColor` 造一个
# 正常撞不上的颜色再塞回 CW.TEXT，看气泡文字跟不跟着换（case_bubbles_painted）。
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                             QMessageBox, QPushButton, QScrollArea,
                             QSizePolicy, QTextEdit, QVBoxLayout, QWidget)

from . import agent
from . import chat as chatmod
from . import chat_store
from . import config as cfgmod
from . import memory
from . import theme
from .paths import ASSETS, PERSONA_DEFAULT, PERSONA_PATH
# 配色只有这一个来源（计划的 Global Constraints）：气泡那四个常量直接引用 toast 的，
# QSS 里的字面量也从它们拼出来。以前这里是手抄的一份，抄漏了两处 —— 窗口底色和系统
# 提示文字跟 toast 已经对不上了，而「以后改桌宠配色聊天窗跟着变」这件事完全没保证。
# 注意 toast.BG 带 α244（气泡是半透明的），聊天窗不需要半透明底，所以 .name() 取的是
# 不带 α 的那个十六进制串；别用 HexArgb，那会把 244 一起带进来。
#
# Task 8 之后除这四档之外的暖色（输入区、轨迹线、玫瑰粉…）统一挪进 theme.py ——
# 这一层名字**不改**：`CW.BG` / `CW.TEXT` 是测试和 demo 直接摸的接口，
# case_bubbles_painted 还会把 CW.TEXT 换掉再看气泡跟不跟。
from .toast import BG, BORDER, TEXT, DIM

BUBBLE_R = 16          # 气泡圆角
TAIL_W = 9             # 尾巴根部宽
TAIL_H = 8             # 尾巴伸出高度
PAD_X = 14             # 气泡内边距（比上一版各多 1px，两行字贴在一起太挤）
PAD_Y = 10
# 贴尾巴那一侧的圆角收小，另一侧放圆 —— 这是「谁在说话」在**形状**上的记号，
# 比只靠颜色区分稳（色弱、截图缩放之后颜色会糊，形状不会）。
CORNER_TAIL = 6        # 尾巴那侧的角
HERS_BG = theme.CARD                   # 她的气泡是暖白实心卡，跟 toast 的半透明底不是一回事
HERS_LINE = BORDER                     # 气泡描边跟 toast 同一个来源
MINE_BG = theme.MINE                   # 用户气泡的粉底，toast 里没有对应物
MINE_LINE = theme.MINE_EDGE            # 它的描边
BAR_BG = theme.BAR                     # 底下那条输入区的底色
BAR_LINE = theme.BAR_EDGE              # 输入区上边线
BTN_BUSY = theme.GHOST                 # 忙时按钮（灰掉，它现在是「停止」）
BTN_BUSY_HOVER = theme.GHOST_HOVER

AVATAR_H = 56          # 头像立绘的高度；宽度按原图比例走，不固定

# 她还没吐第一个字时气泡里显示的东西。**会一帧一帧走**。
#
# `_start_reply` 先把气泡建出来、首块到了才填字，中间这段时间（DeepSeek 通常 1~3 秒，
# 调工具的时候更久）屏幕上是一个**空气泡** —— 看着像界面卡住了。给个占位。
#
# 三帧是等宽钉死的（见 `_Bubble.set_text` 的 pin）：宽度按内容撑，一胀一缩会让整行
# 左右跳。也别改成「正在输入…」那种长提示 —— 同理，而且它比三个点更吵。
TYPING_FRAMES = ("·", "··", "···")
TYPING_MS = 420        # 走一格的时间。太快像在抖，太慢又回到「卡住了」的观感
TYPING = TYPING_FRAMES[0]      # 气泡刚建出来、定时器还没走过第一格时的样子

# 快捷开场。只在**没有任何记录**的时候出现，点一下就替用户把话发出去。
#
# 挑这三句是有目的的：前两句各自会逼出她一个工具（`now` / `remember`），
# 第三句是纯聊天。第一次打开的人不知道她能干什么、也不知道该说什么，
# 一行字把「她能查时间、能记事、也能瞎聊」三件事摆出来。
#
# **长短是有约束的**：三个按钮得并排塞进窗口最窄的那一档（setMinimumSize 的
# 320）。第一版第二句写的是「记住我在做塔菲桌宠」，420 宽就被切掉了最后一个字。
# 改文案之前先按 320 算一遍宽度，别只看自己屏幕上开着的那个尺寸。
SUGGESTS = ("现在几点？", "记住我爱熬夜", "陪我聊两句")

# 消息区里「她调过工具」那一行小字的控件类型。单独定一个类是为了能
# `findChildren(_Trace)` 一次全找出来调宽度 —— 和 `findChildren(_Bubble)` 一个用法。
TRACE_OBJECT = "toolTrace"

# 日期分隔线那个 QLabel 的 objectName。测试靠它把「日期分隔线」和「气泡/系统提示」
# 分开数（见 tools/test_chat_window.py 的 texts()/rows()），改这个名字要一起改。
DAY_OBJECT = "daySeparator"

# QSS 里的 {} 是它自己的语法，写在这个 f-string 里得翻倍
QSS = f"""
#chatRoot, QScrollArea, #chatArea {{ background: {BG.name()}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 8px 2px 8px 0; }}
QScrollBar::handle:vertical {{
    background: rgba({BORDER.red()}, {BORDER.green()}, {BORDER.blue()}, 150);
    border-radius: 3px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba({MINE_BG.red()}, {MINE_BG.green()}, {MINE_BG.blue()}, 190);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
#chatBar {{ background: {BAR_BG.name()}; border-top: 1px solid {BAR_LINE.name()}; }}
#chatInput {{
    background: {theme.FIELD.name()}; border: 1.4px solid {theme.FIELD_EDGE.name()};
    border-radius: 16px; padding: 8px 12px; color: {TEXT.name()};
    selection-background-color: {MINE_BG.name()}; selection-color: {theme.MINE_INK.name()};
}}
#chatInput:focus {{ border: 1.4px solid {theme.FIELD_FOCUS.name()}; }}
#sendBtn {{
    background: {MINE_BG.name()}; color: {theme.MINE_INK.name()};
    border: none; border-radius: 15px;
    font-family: 'Microsoft YaHei UI'; font-size: 10pt;
}}
#sendBtn:hover {{ background: {MINE_LINE.name()}; }}
#sendBtn:pressed {{ background: {theme.MINE_DEEP.name()}; }}
#sendBtn[busy="true"] {{ background: {BTN_BUSY.name()}; }}
#sendBtn[busy="true"]:hover {{ background: {BTN_BUSY_HOVER.name()}; }}
#suggestBar {{ background: {BAR_BG.name()}; }}
#suggestBtn {{
    background: {theme.CHIP.name()}; color: {TEXT.name()};
    border: 1px solid {theme.CHIP_EDGE.name()}; border-radius: 13px; padding: 4px 9px;
    font-family: 'Microsoft YaHei UI'; font-size: 8.5pt;
}}
#suggestBtn:hover {{
    background: {MINE_BG.name()}; color: {theme.MINE_INK.name()};
    border-color: {MINE_LINE.name()};
}}
#suggestBtn:pressed {{ background: {MINE_LINE.name()}; border-color: {MINE_LINE.name()}; }}
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


def _round_rect(r: QRectF, tl: float, tr: float, br: float, bl: float) -> QPainterPath:
    r"""四个角**各画各的**圆角矩形。

    QPainterPath.addRoundedRect 四个角只能一样大，而气泡要的是「贴着尾巴那一侧
    收小」—— 那是「谁在说话」的形状记号。手搓一遍 arcTo：角度按 Qt 的老规矩，
    0° 在三点钟、逆时针为正，所以每一段都从 -90° 扫过去。
    """
    p = QPainterPath()
    p.moveTo(r.left() + tl, r.top())
    p.lineTo(r.right() - tr, r.top())
    if tr:
        p.arcTo(QRectF(r.right() - 2 * tr, r.top(), 2 * tr, 2 * tr), 90, -90)
    p.lineTo(r.right(), r.bottom() - br)
    if br:
        p.arcTo(QRectF(r.right() - 2 * br, r.bottom() - 2 * br, 2 * br, 2 * br), 0, -90)
    p.lineTo(r.left() + bl, r.bottom())
    if bl:
        p.arcTo(QRectF(r.left(), r.bottom() - 2 * bl, 2 * bl, 2 * bl), 270, -90)
    p.lineTo(r.left(), r.top() + tl)
    if tl:
        p.arcTo(QRectF(r.left(), r.top(), 2 * tl, 2 * tl), 180, -90)
    p.closeSubpath()
    return p


class _Body(QLabel):
    r"""气泡里的正文。

    唯一的作用是**改掉 sizeHint 的宽度**：QLabel 打开 wordWrap 之后会自己搜一个
    「高度最小的最窄宽度」，短句常常被当成两行折 —— 折出来的第二行只有两三个字，
    右边还空着一大块，整条气泡看着像没对齐。这儿直接报「最长那一行的整行宽度」，
    一行放得下就不折；真超过气泡宽度上限时，超出的部分由布局走 heightForWidth
    重新算高度（QLabel::setWordWrap 会把 heightForWidth 打开），照样折得对。

    +2 是**故意留的**：宽度正好等于文字宽度时 QLabel 会因为亚像素误差折行，
    留两像素的余量它才肯老老实实放一行。
    """

    PAD = 2

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        lines = (self.text() or " ").split("\n")
        w = max(fm.horizontalAdvance(ln) for ln in lines)
        # 高度按不折行算：宽度没被砍时就是这个高度；被砍了走 heightForWidth。
        return QSize(w + self.PAD, fm.height() * len(lines))


class _Bubble(QWidget):
    """一个气泡。圆角矩形加一条小尾巴，尾巴只给她的消息 —— 用户的消息靠右，
    右边贴边没有空间伸尾巴，而且有头像的一侧本来就需要这个锚点。

    自己画而不是用 QSS：QSS 画不出尾巴，而跟 toast.py 保持一致的那套画法
    这里是现成的。
    """

    def __init__(self, text: str, mine: bool, parent=None):
        super().__init__(parent)
        self.mine = mine
        # 她调工具留下的那几行小字（`_Trace` 控件）。**只有这一个列表** ——
        # 落盘时从控件的 text() 现读，不另存一份字符串，两份迟早对不上。
        self.traces = []
        self._label = _Body(text, self)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._label.setFont(QFont("Microsoft YaHei UI", 10))
        # 打字动画那几帧宽度不等，钉住最宽的那帧。见 set_text。
        # 那个 +PAD 跟 _Body.sizeHint 是同一笔余量 —— 少加的话钉住的那一帧会被
        # sizeHint 顶宽 2px，气泡照样一格一胀。
        self._pin_w = (self._label.fontMetrics().horizontalAdvance(TYPING_FRAMES[-1])
                       + _Body.PAD)
        # 她的气泡文字跟 toast 同一个来源（这条 inline stylesheet 独立于模块级 QSS，
        # 手抄一份的话「改桌宠配色聊天窗跟着变」就又断在这儿）。白字那半边没有对应物：
        # 粉底上的白字 toast 里不存在，写死。
        self._label.setStyleSheet(
            f"color: {theme.MINE_INK.name() if mine else TEXT.name()};"
            " background: transparent;")

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

    def set_text(self, text: str, pin: bool = False) -> None:
        r"""换掉气泡里的字。

        `pin=True` 是给打字动画用的：那三帧（·/··/···）宽度不等，气泡是按内容撑的，
        不钉住就会一格一胀一缩、整行左右跳 —— 正是 TYPING_FRAMES 上面那条注释说的
        毛病。钉的时候按**最宽**那帧定，所以点最少的时候气泡也不会缩回去。

        真字到了就 pin=False 放开：`setMinimumWidth(0)` 之后 QLabel 照旧按自己的
        sizeHint 走，长句子该折行还是折行。
        """
        self._label.setText(text)
        self._label.setMinimumWidth(self._pin_w if pin else 0)
        self.updateGeometry()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x = 0 if self.mine else TAIL_W
        body = QRectF(x + 0.5, 0.5, self.width() - x - 1, self.height() - 1)
        if self.mine:
            # 我这边靠右：尾巴那侧是右下角，收小一圈
            path = _round_rect(body, BUBBLE_R, BUBBLE_R, CORNER_TAIL, BUBBLE_R)
        else:
            path = _round_rect(body, CORNER_TAIL, BUBBLE_R, BUBBLE_R, BUBBLE_R)
            # 尾巴尖对准**第一行字的竖直中点**，指回左边的头像。写死一个偏移量
            # 会错：正文那个 QLabel 自己上下还带一截留白，一行字的中点在 PAD_Y 往下
            # 半行的地方，不是 PAD_Y。矮气泡（「…」那一档只有一行）再压到一半高度。
            tail = QPainterPath()
            y = min(PAD_Y + self._label.fontMetrics().height() / 2.0, body.height() / 2)
            tail.moveTo(body.left() - TAIL_H + 1, y)
            tail.lineTo(body.left() + 1, y - TAIL_W / 2)
            tail.lineTo(body.left() + 1, y + TAIL_W / 2)
            tail.closeSubpath()
            path = path.united(tail)
        p.setPen(QPen(MINE_LINE if self.mine else HERS_LINE, 1.2))
        p.setBrush(MINE_BG if self.mine else HERS_BG)
        p.drawPath(path)


class _Trace(QLabel):
    """她调完工具留在气泡下面的一行小字。

    单独一个类没有别的作用，就是要一个能 `findChildren` 的类型 ——
    `resizeEvent` 里要一次把所有轨迹行的宽度上限都刷一遍，混在普通 QLabel 里
    挑不出来（日期分隔线、系统提示也都是 QLabel）。
    """


class _Avatar(QLabel):
    """她的头像。

    就是 `_avatar_pixmap()` 那张立绘（26×56 的全身像，比例不动），只是在它**背后**
    垫一块淡淡的粉 —— 立绘自己是透明底的，直接贴在奶油色消息区上时，她那身深色
    马甲会看着像一小块脏点；垫一层同色系的圆角之后它才像「一枚头像」而不是「一块
    没抠干净的图」。

    单独一个类只为这个 paintEvent。控件尺寸仍然是立绘的尺寸（测试按这个查
    「头像框贴不贴立绘」，见 case_bubbles_painted），所以这块底不额外占宽。
    """

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.AVATAR_BG)
        p.drawRoundedRect(r, 12, 12)
        p.setPen(QPen(theme.AVATAR_EDGE, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 12, 12)
        super().paintEvent(e)


class _Day(QLabel):
    """日期分隔线：一颗居中的小胶囊。

    直接一段灰字悬在消息中间太像「一条内容」，而它其实是个**路标**。给它一块
    自己的底，跟气泡、轨迹行区分开。

    仍是 QLabel 的子类、仍然直接把 objectName 打在自己身上 —— 测试按
    `msgs` 顶层控件的 objectName 认它（`texts()` / `rows()` / case_day_separator），
    套一层容器就全认不出来了。
    """

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.text()) + 26
        h = fm.height() + 8
        # 画在 contentsRect 里，不是整个控件里 —— 上下那点留白是
        # setContentsMargins 加的，字也跟着它缩，胶囊得跟着同一块矩形才对得齐。
        cr = self.contentsRect()
        r = QRectF(cr.center().x() - w / 2.0, cr.center().y() - h / 2.0, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.DAY_BG)
        p.drawRoundedRect(r, h / 2.0, h / 2.0)
        super().paintEvent(e)


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


def full_ts(ts: str) -> str:
    r"""气泡上鼠标悬停显示的那行时间；算不出来返回空串（跟 day_label 一个约定）。

    日期分隔线只精确到天，想看「这句是上午还是半夜说的」就没有别的途径了。
    悬停不动版面，也不会把消息区撑高。
    """
    try:
        d = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return ""
    return f"{d.year}-{d.month:02d}-{d.day:02d} {d.hour:02d}:{d.minute:02d}"


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
        # 打字动画。挂在窗口上，所以窗口析构时它一起没，不用自己收。
        self._typing_i = 0
        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(TYPING_MS)
        self._typing_timer.timeout.connect(self._tick_typing)

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
        self.msgs.setContentsMargins(16, 16, 16, 16)
        self.msgs.setSpacing(12)
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
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(10)
        self.input = ChatInput(self.send)
        self.input.setObjectName("chatInput")
        # 提示语**必须短**：最窄的 420 宽窗口里，输入框的可用文本宽只有约 268px，
        # 而「和 taffy 说点什么…（回车发送，Shift+回车换行）」量出来是 423px ——
        # 超了 1.6 倍，任何窗口宽度下都折成两行，占满输入框还挤掉了首行。
        # 两条快捷键提示挪到 tooltip：常看常烦的东西不该常驻，但也不能丢。
        self.input.setPlaceholderText("和 taffy 说点什么…")
        self.input.setToolTip("回车发送，Shift+回车换行")
        self.input.setFixedHeight(68)
        row.addWidget(self.input, 1)

        self.btn = QPushButton("发送")
        self.btn.setObjectName("sendBtn")
        # 不再是 72×72 的方块 —— 跟输入框同高的话它是一整块粉色，压得比输入框还重。
        # 78×44 的圆角片，竖直居中在输入框旁边，视觉重量刚好反过来。
        self.btn.setFixedSize(78, 44)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self._on_button)
        row.addWidget(self.btn)

        # 快捷开场在输入框**上面**：它是「帮你开口」的一句话，不是输入区的一部分，
        # 挤在发送按钮下面会跟输入框抢位置。
        self.suggests = self._build_suggests()
        root.addWidget(self.suggests)
        root.addWidget(bar)

        self.setStyleSheet(QSS)

    def _build_suggests(self) -> QWidget:
        """快捷开场那一行。空记录时才有，见 _update_suggests。"""
        wrap = QWidget()
        wrap.setObjectName("suggestBar")
        row = QHBoxLayout(wrap)
        # 边距和间距是**算过**的：三个按钮加上它们要塞进窗口最窄的 320
        # （见 SUGGESTS 上面那段和 case_suggest_buttons）。别顺手加大。
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(6)
        # 两边都留弹簧 = 这一行居中。只留右边那个的话三个按钮齐刷刷靠左，
        # 右边空出一大片，而下面的输入框和上面的气泡都是通栏的 —— 看着像漏排了。
        # 居中不改变宽度之和，所以 case_suggest_buttons 的「塞得进 320」照样成立。
        row.addStretch(1)
        for text in SUGGESTS:
            b = QPushButton(text)
            b.setObjectName("suggestBtn")
            b.setCursor(Qt.PointingHandCursor)
            # 默认参数把 text 绑死在**这一轮**循环的值上。用闭包直接引用 text 的话
            # 三个按钮最后都发最后那句 —— 经典坑，别改。
            b.clicked.connect(lambda _checked=False, t=text: self._use_suggest(t))
            row.addWidget(b)
        row.addStretch(1)
        wrap.setVisible(False)         # 由 _update_suggests 定
        return wrap

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
        self._uncenter_notice()
        # 插在弹簧前面，否则新消息会跑到下面去
        self.msgs.insertWidget(self.msgs.count() - 1, widget)

    def _uncenter_notice(self) -> None:
        r"""把「顶到中间」的欢迎语打回原形。

        `_center_notice` 让那条提示吸走全部剩余空间。真消息一来就得还原 ——
        不还原的话它会一直撑着，消息全被挤到窗口底下，而且末尾那根弹簧也失效了。
        以「竖直方向 Expanding」认人：消息区里只有欢迎语会被设成那样（气泡是
        Maximum、提示是 Preferred），不用另存一个引用。
        """
        for i in range(self.msgs.count()):
            it = self.msgs.itemAt(i)
            w = it.widget() if it is not None else None
            if w is not None and w.sizePolicy().verticalPolicy() == QSizePolicy.Expanding:
                w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        last = self.msgs.count() - 1
        sp = self.msgs.itemAt(last).spacerItem() if last >= 0 else None
        if sp is not None:
            sp.changeSize(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
            self.msgs.setStretch(last, 1)

    def _limit_w(self) -> int:
        """一条气泡 / 一行轨迹能占多宽。窗口可以缩放，写死像素必然错。"""
        return int(self.scroll.viewport().width() * 0.75)

    def _apply_width_to(self, bubble) -> None:
        """只给这一个气泡（和它的轨迹行）设 75% 上限。

        加一条气泡就全量刷一遍的话是 O(n²)：回填 200 条历史时每条都要遍历一遍
        已经建出来的所有气泡，实测开窗要 2 秒白屏（原生 5 秒）。新加的那条只需要
        它自己这一次。
        """
        w = self._limit_w()
        bubble.setMaximumWidth(w)
        for t in bubble.traces:
            t.setMaximumWidth(w)

    def _apply_bubble_width(self) -> None:
        """全量刷一遍，只在 resizeEvent 里调 —— 窗口缩放之后所有气泡的上限都得跟着变。

        轨迹行得单独找一遍：它们是气泡的**兄弟**（在同一个 holder 的竖排里），
        不是气泡的子控件，`findChildren(_Bubble)` 一个都够不着。
        """
        w = self._limit_w()
        for b in self.area.findChildren(_Bubble):
            b.setMaximumWidth(w)
        for t in self.area.findChildren(_Trace):
            t.setMaximumWidth(w)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._apply_bubble_width()

    # ---------- 渲染 ----------
    def _render_history(self) -> None:
        self._last_day = ""
        for m in self.history:
            self._maybe_day(m.get("ts", ""))
            bubble = self._append_widget(m["content"], m["role"] == "user",
                                         m.get("ts", ""))
            # 重放她当时调过的工具。`trace` 是后加的字段，旧记录里没有 ——
            # 所以是 .get 不是 []，而且不认的字段必须当没有，不能报错。
            for line in m.get("trace") or []:
                self._add_trace(bubble, line)
        self._update_suggests()
        if not self.history:
            self._append_notice("和 taffy 打个招呼吧喵")
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _update_suggests(self) -> None:
        """快捷开场只在**一句都没聊过**的时候露面。

        聊过之后还杵在那儿的话，它就不是「帮你开口」而是「占着地方」了。
        清空记录之后要重新露出来 —— clear_history 会再调一次。
        """
        self.suggests.setVisible(not self.history)

    def _maybe_day(self, ts: str) -> None:
        """跨天了就插一条日期分隔。同一天连着多少条都只插一次。"""
        lab = day_label(ts)
        if lab and lab != self._last_day:
            self._last_day = lab
            self._append_day(lab)

    def _append_day(self, text: str) -> None:
        lab = _Day(text)
        # 打个标记。日期分隔线是消息区里**第三类**顶层控件（气泡、系统提示之外的），
        # 测试里数「行数/气泡数」的地方按老口径数的是前两类，没这个标记就得靠
        # 「今天」这两个字去认，改文案就崩。
        lab.setObjectName(DAY_OBJECT)
        lab.setAlignment(Qt.AlignCenter)
        # 上下那点留白用 setContentsMargins 而不是 QSS 的 padding：胶囊是按
        # contentsRect 画的（见 _Day.paintEvent），QSS 的 padding 不进 contentsRect。
        lab.setContentsMargins(0, 10, 0, 2)
        lab.setStyleSheet(f"color: {DIM.name()};"
                          " font-family: 'Microsoft YaHei UI'; font-size: 8.5pt;"
                          " background: transparent;")
        self._add(lab)

    def _append_widget(self, text: str, mine: bool, ts: str = ""):
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
        when = full_ts(ts)
        if when:
            bubble.setToolTip(when)
        if mine:
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            avatar = _Avatar()
            avatar.setStyleSheet("background: transparent;")
            pm = _avatar_pixmap()
            if not pm.isNull():
                avatar.setPixmap(pm)
                # 跟着缩放后的立绘走，不写死 56。原图 369×800 缩到 56 高只有 26 宽，
                # 框成 56×56 的话右边会空出 30px，正好夹在头像和气泡中间把它们推远。
                avatar.setFixedSize(pm.width(), pm.height())
            # 她这边是**竖排**：气泡在上，工具轨迹行在下。用户那边没有轨迹，
            # 也就不需要这一层，直接放气泡。
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(3)
            col.addWidget(bubble)
            row.addWidget(avatar, 0, Qt.AlignTop)
            row.addLayout(col)
            row.addStretch(1)
            bubble._col = col
        bubble._outer = holder          # 中途要撤销的时候删的是这个，不是 bubble
        self._add(holder)
        # 新气泡也得吃到 75% 上限。只靠 resizeEvent 的话，窗口最后一次缩放之后
        # 产生的气泡拿的是默认上限，长消息会超出去。只刷这一个 —— 全量刷一遍是
        # O(n²)，见 _apply_width_to。
        self._apply_width_to(bubble)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return bubble

    def _add_trace(self, bubble, text: str) -> None:
        r"""在她那条气泡下面补一行「她干了什么」。

        她调工具那几秒里气泡一直显示「…」，屏幕上完全看不出她在忙 —— 和「卡住了」
        长得一模一样。事后留一行小字，这是「会聊天的套壳」和「真的做了点事的智能体」
        之间最直观的那条分界。

        **写的是工具返回的那句话本身**，不另编一套措辞：`agent.run` 的返回值已经
        是「记住了：X」/「现在是…」这种给人看的中文，再抄一份到这儿就是两处要一起改。
        它同时也是**唯一**诚实的来源 —— 工具没写成功时返回的是「没记住（写不进文件）」，
        自己编一句「记住了」就成了撒谎。
        """
        if bubble is None or not getattr(bubble, "_col", None):
            return
        lab = _Trace(text)
        lab.setObjectName(TRACE_OBJECT)
        lab.setWordWrap(True)
        # 左边一条竖线，是「这是旁白不是她说的话」最省事的画法。
        # 内缩 PAD_X + TAIL_W 让竖线正好落在气泡**正文**的左边缘上 —— 不缩的话
        # 它会从窗口最左边起，看着像另一条消息而不是这条气泡的注脚（画出来对过）。
        #
        # 8pt + DIM：旁白得**明显**小于正文（正文 10pt），小半档的话它读起来还是
        # 「她说的另一句话」。线也换成了淡玫瑰，原来的 BORDER 在奶油底上是一条
        # 实打实的粉杠，比它标注的那行字还显眼。
        lab.setStyleSheet(
            f"color: {DIM.name()}; font-family: 'Microsoft YaHei UI'; font-size: 8pt;"
            f" border-left: 2px solid {theme.RULE.name()}; padding-left: 9px;")
        lab.setContentsMargins(PAD_X + TAIL_W, 0, 0, 0)
        lab.setMaximumWidth(self._limit_w())
        bubble._col.addWidget(lab)
        bubble.traces.append(lab)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _append_notice(self, text: str) -> None:
        lab = QLabel(text)
        lab.setAlignment(Qt.AlignCenter)
        lab.setWordWrap(True)
        lab.setStyleSheet(f"color: {DIM.name()}; font-family: 'Microsoft YaHei UI';"
                          " font-size: 9.5pt; padding: 6px 10px;"
                          " background: transparent;")
        self._add(lab)
        # 一句都没聊过的时候（消息区里只有这一条），把它顶到竖直中间。不然整块留白
        # 全堆在下半屏，开窗那一下看着像界面还没加载完 —— 这是用户见到桌宠的第一眼。
        if self.msgs.count() <= 2:      # 这一条 + 末尾那根弹簧
            self._center_notice(lab)

    def _center_notice(self, lab) -> None:
        r"""把消息区里唯一的那条提示顶到竖直中间。

        **不往布局里塞前置弹簧**：`msgs` 的项数是测试盯着的契约
        （case_clear_history：「消息区就是弹簧 + 一句提示」== 2 项），多一根就红。
        改成让这条提示自己吃下全部剩余空间（Expanding），末尾那根弹簧同时降级成
        Minimum —— 空间只有它一个人要，字就落在正中间，项数一项没多。
        """
        lab.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        last = self.msgs.count() - 1
        sp = self.msgs.itemAt(last).spacerItem()
        if sp is not None:
            sp.changeSize(0, 0, QSizePolicy.Minimum, QSizePolicy.Minimum)
            self.msgs.setStretch(last, 0)

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
        self._append_widget(text, True, msg["ts"])
        chat_store.save(self.history)
        self._update_suggests()
        self._start_reply()

    def _use_suggest(self, text: str) -> None:
        """点快捷开场：把那句话填进输入框，然后走**正常那条发送路径**。

        不复用 send() 里那套逻辑，也不绕开它 —— 绕过的话「正在回复时不能重发」
        那道守卫就失效了。
        """
        if self.worker is not None:
            return
        self.input.setPlainText(text)
        self.send()

    def _start_reply(self) -> None:
        self.pending_text = ""
        # 占位符不是内容：`pending_text` 照旧从空开始攒，气泡里先显示个「·」，
        # 首块一到就被 _on_chunk 覆盖掉。
        self.pending = self._append_widget(TYPING, False)
        self._typing_i = 0
        self._typing_timer.start()
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
        """她要求调工具：执行 -> 记一行轨迹 -> 把往返记进脚手架 -> 带着结果再问一轮。

        注意这条路上**不碰 `pending_text` 的内容**：工具往返期间气泡里一直是
        打字动画，因为她确实还没开始说话。
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
            # 轨迹写的是 result 原话（见 _add_trace）。**没写进去的往返不留痕** ——
            # 界面说「她记住了X」而盘上没这条，比不显示更糟。
            self._add_trace(self.pending, result)
        # 她调工具那一轮可能先吐了句「让我想想…」再调，那些字已经流进 pending_text 和
        # 气泡了。不在这儿清掉的话，下一轮的字会**接在后面**，而 _on_done 最后是用
        # 完整文本覆盖气泡的 —— 屏幕上会先看到「让我想想…答」，然后跳成「答」。
        #
        # 打字动画**不停**：轨迹行冒出来之后她还是一个字没说，屏幕上仍然该是「她在忙」。
        self.pending_text = ""
        if self.pending is not None:
            self.pending.set_text(TYPING, pin=True)
        self.worker = None
        self._launch()

    def _on_chunk(self, piece: str) -> None:
        # 只管填字，滚动交给 _on_range_changed —— 气泡长高之后 rangeChanged 才带着
        # 新算出来的 maximum 过来，在这儿读到的永远是旧值（Critical 1）。
        self._stop_typing()          # 真字到了，不要再拿点去盖它
        self.pending_text += piece
        if self.pending is not None:
            self.pending.set_text(self.pending_text)

    def _tick_typing(self) -> None:
        r"""走一格打字动画。

        `pending_text` 一旦非空就自己停下 —— 那一刻气泡里已经是她的话了，
        再往上盖点就成了「说着说着突然变回点」。这条判断比在 `_on_chunk` 里
        掐定时器更要紧：`_on_tools` 会把 `pending_text` 清回空，那之后动画
        本来就该继续走。
        """
        if self.pending is None or self.pending_text:
            self._stop_typing()
            return
        self._typing_i = (self._typing_i + 1) % len(TYPING_FRAMES)
        self.pending.set_text(TYPING_FRAMES[self._typing_i], pin=True)

    def _stop_typing(self) -> None:
        self._typing_timer.stop()

    def _on_done(self, full: str) -> None:
        self.worker = None
        self._set_busy(False)
        if not full.strip():
            self._drop_pending()                 # 用户点了停止，一个字都没吐出来
            return
        self._stop_typing()
        if self.pending is not None:
            self.pending.set_text(full)          # 用完整文本覆盖，跟落盘的一致
        rec = chat_store.make("assistant", full)
        # 轨迹跟着一起落盘。只写在界面上、不存的话，重开窗口那几行就凭空消失了 ——
        # 和 `_on_fail` 里那段「半句气泡」的道理完全一样（界面和落盘对不上）。
        # `build_messages` 只取 role/content，这个额外的键不会进请求。
        traces = [t.text() for t in (self.pending.traces if self.pending else [])]
        if traces:
            rec["trace"] = traces
        self.history.append(rec)
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
        # 打字动画先停：下面马上要把那个气泡删掉，定时器再走一格就是在碰已删控件。
        self._stop_typing()
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
        # 定时器在这儿也要停：`_stop_worker` 在没有 worker 时会早退（关窗时常见），
        # 那时它下面那次 _drop_pending 根本不跑，动画会对着已关的窗口继续走。
        self._stop_typing()
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
        self._update_suggests()          # 记录清空了，快捷开场该回来
        self._append_notice("清空了喵，重新开始")
