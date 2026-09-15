#!/usr/bin/env python
r"""录一段白底演示视频：`_work\demo\taffy-demo.mp4`。

    python tools\demo.py            # 出一整支
    python tools\demo.py --still 21 # 只出第 21 秒那一帧，用来对着看排版

### 为什么是「Qt 自己画」而不是录屏

要的是**白底**。这台机器的桌面不是白的，录屏会把壁纸、任务栏、别的窗口一起录
进去 —— 那是**隐私**，不是演示。现成的录屏工具也没有「只录一个窗口、顺便把背景
换成纯白」这种能力。

所以换一条路：桌宠本来就是 Qt 画的，那就让 Qt 把每一帧画到一张白底画布上，再
交给 ffmpeg 编码。好处是

- 背景是纯白，跟桌面上有什么完全无关；
- **一个窗口都不弹**（`WA_DontShowOnScreen`），录的时候不用把机器空出来；
- 可重放：同一份素材每次跑出来一模一样，不像录屏会被系统动画、通知弹窗搅了。

### 它跟真程序的关系

用的是**真控件**：`PetWindow`、`Toast`、`ChatWindow` 全是生产代码，立绘、气泡、
呼吸、跳舞、聊天窗的排版一点没改。假掉的只有两样：

- **网络**。聊天那一段不连 DeepSeek —— 要花钱，而且不可重放。气泡里的字是按
  时序喂进去的，走的是 `ChatWindow` 里真正那几个内部方法（`_append_widget` /
  `_add_trace`），画出来跟真聊一遍没有区别。
- **时钟**。动画器的心跳（`QTimer`）停掉，改成按帧号手动拨 —— 25fps 要的是
  确定的那一帧，不能靠真实时钟。

工具轨迹那一行是**真**的：调的就是 `agent.run("now", …)` 和
`agent.run("remember", …)`。后者会写文件，所以先把它指到临时目录（见 `_isolate`）。

### 数据安全

这个脚本**绝不碰**用户的真实数据。开工前先把 `config.save` / `chat_store.save`
换成空函数、把 `CHAT_PATH` / `MEMORY_PATH` 指到临时目录，配置用一份现造的字面量
（`DEMO_CFG`，里面没有 `api_key`）。用户的 Key、聊天记录、余额不会进这个进程，
自然也就不会进视频。见 `_isolate()`。

注意 `ChatWindow.closeEvent` 里有 `cfgmod.save(self.cfg)` —— 不隔离的话，光是把
演示窗口关掉就会把这份假配置**覆盖到真的 config.json 上**。那正是 `_isolate` 存在的
理由，不是可选的加固。
"""
import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import PyQt5 之前设。offscreen 平台**画不出字形**（出来是一排方框，
# 气泡、按钮全是空的），见 tools/test_chat_window.py 的 _shot。原生平台上配
# WA_DontShowOnScreen 才是「真渲染、不弹窗」。
os.environ["QT_QPA_PLATFORM"] = "windows"

from PyQt5.QtCore import Qt, QEventLoop, QTimer, QRect
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import QApplication

from taffy_pet import agent
from taffy_pet import anim as animmod
from taffy_pet.chat_window import TYPING, ChatWindow
from taffy_pet.pet import PetWindow
from taffy_pet.paths import VOICE_DIR

# ---------- 画面 ----------
W, H, FPS = 1280, 720, 25
SPF = 1.0 / FPS

WHITE = QColor(255, 255, 255)
INK = QColor(58, 46, 52)          # 跟 toast.py 的 TEXT 同一个色，标题正文一套
DIM = QColor(150, 120, 132)       # 跟 toast.py 的 DIM 同一个色
PINK = QColor(228, 150, 175)      # 跟 chat_window.py 的 MINE_BG 同一个色

PET_H = 420                       # 她在视频里多大。比默认的 200 大一倍多，720p 上才看得清
PET_BOTTOM = 662                  # 她的脚落在画布哪一行
PET_CX_SOLO = W // 2              # 单人镜头的水平中心
PET_CX_CHAT = 300                 # 聊天镜头的水平中心（靠左，右边留给窗口）

CHAT_X, CHAT_Y = 720, 90          # 聊天窗在画布上的位置
CHAT_W, CHAT_H = 420, 560

SR = 24000                        # 语音库就是 24kHz 单声道 16bit，别重采样，白丢精度

# 演示里「现在几点」那个工具返回的时刻，见 _isolate 里的时钟钉死。
# 挑晚上是为了跟「关注塔菲喵」那套熬夜的台词对得上。
DEMO_NOW = "现在是 2026 年 9 月 15 日 22:32（晚上，星期二）"

# ---------- 时间轴（秒）----------
# 每一段的首尾写在这儿，下面每个 scene 函数只管自己那一段内部的进度。
T_TITLE = (0.0, 4.0)
T_IDLE = (4.0, 7.0)
T_ALONE = (7.0, 13.0)      # 没人理她，她自己动了
T_TALK = (13.0, 19.0)
T_DANCE = (19.0, 25.5)
T_CHAT = (25.5, 43.5)
T_OUT = (43.5, 47.0)
TOTAL = T_OUT[1]

# 她说的那句话之一。**刻意不写死时间**（「现在是 22 点」）：轨迹行里已经是
# 真的时间了，回复里再写一个对不上就成了自己打自己的脸。
TALK_SPEECH = "关注塔菲喵关注塔菲谢谢喵"
TALK_BALANCE = "余额 ¥42.00"       # 演示用的假数字，不是任何人的真实余额
# 她自己找事做时冒出来的那句。**取自语音库里的原句**（voice/14.wav），不是编的 ——
# 演示里出现的话要么是真的，要么就别出现。
ALONE_LINE = "塔菲在看着你哦。"

DEMO_CFG = {
    # 没有 api_key。这一条是**故意的** —— 演示脚本不该有能力碰到用户的 Key
    "api_key": "",
    "height": PET_H,
    "opacity": 1.0,
    "always_on_top": True,
    "blink": True,
    "speech": TALK_SPEECH,
    "hint_shown": True,
    "volume": 1.0,
    "voice": True,
    "agent": True,
    "idle": True,
    "pos": None,
    "chat_geometry": None,
}


def _pump(ms: int = 60) -> None:
    """空转事件循环 `ms` 毫秒。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


def _grab(w) -> QPixmap:
    """抓一张，并且**强制 dpr=1**。

    系统缩放 150% 的话 `grab()` 会给一张 1.5 倍大的图，直接贴到 1280x720 的画布上
    她就变成半截。渲染前已经关掉了 HiDPI 缩放，这里是第二道保险。
    """
    pm = w.grab()
    if pm.devicePixelRatio() != 1.0:
        dpr = pm.devicePixelRatio()
        pm = pm.scaled(int(pm.width() / dpr), int(pm.height() / dpr),
                       Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(1.0)
    return pm


# ============================================================ 隔离
def _isolate() -> Path:
    r"""把「写盘」这件事整个挪到临时目录，返回那个目录。

    **这是这个脚本最重要的一段。** 源码运行时 `paths.DATA_DIR` 是项目根目录，
    也就是 `chat_store.CHAT_PATH` / `config.CONFIG_PATH` / `memory.MEMORY_PATH`
    全都指向仓库里那几个真文件 —— 里面是用户的 API Key、全部聊天记录。

    三个必须堵掉的出口：

    1. `chat_store.load()` 会把真记录铺进聊天窗 —— 那就**录进视频**了。
    2. `ChatWindow.closeEvent` → `cfgmod.save()` 会把这份假配置覆盖到真的
       `config.json` 上，用户的 Key 就此消失。
    3. `agent.run("remember", …)` 会往真的 `memory.md` 里写一条演示用的假记忆。

    模块级的 `from .paths import CHAT_PATH` 是在 import 时绑死的，改 `paths` 没用，
    得直接改**那个模块**里的名字。
    """
    from taffy_pet import chat_store, config, memory

    tmp = Path(tempfile.mkdtemp(prefix="taffy-demo-"))
    chat_store.CHAT_PATH = tmp / "chat.json"
    memory.MEMORY_PATH = tmp / "memory.md"
    config.save = lambda _cfg: None                    # 谁也别想覆盖真 config.json
    chat_store.save = lambda _msgs: None

    # 「现在几点？」那条轨迹是**真调** agent 出来的，也就真的会写出录制那一刻的
    # 年月日时分。发在 GitHub 上的视频没有理由带着这个时间戳。把表拨到死，
    # 出图还是走 `agent._now` 那条真路径（措辞、星期几的算法、时段判断全照旧），
    # 只是数字固定 —— 顺带整支片子变成可重放的。
    agent._now = lambda: DEMO_NOW

    print(f"[demo] 数据隔离到 {tmp}")
    return tmp


# ============================================================ 声音
def build_audio(clips, out_path: Path) -> None:
    r"""把几段 wav 按给定时刻混进一条静音轨，写成 24kHz 单声道 16bit。

    `clips` 是 `[(起始秒, 文件名, 增益)]`。**不用 ffmpeg 的 amix** —— 那要写
    一串 filter 表达式，还得先各自算 delay；这里总共就几段，直接按采样点相加
    更直白，也更容易看出「什么时候该响」。
    """
    total = int(TOTAL * SR)
    mix = array("h", bytes(total * 2))          # 全零 = 静音
    for at, name, gain in clips:
        p = VOICE_DIR / name
        if not p.exists():
            print(f"[demo] 没有 {p}，这一段跳过")
            continue
        with wave.open(str(p)) as w:
            if w.getframerate() != SR or w.getnchannels() != 1:
                print(f"[demo] {name} 不是 {SR}Hz 单声道，跳过")
                continue
            data = array("h", w.readframes(w.getnframes()))
        off = int(at * SR)
        for i, s in enumerate(data):
            j = off + i
            if j >= total:
                break
            v = mix[j] + int(s * gain)
            mix[j] = -32768 if v < -32768 else (32767 if v > 32767 else v)

    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(mix.tobytes())
    print(f"[demo] 音轨 {out_path.name}（{TOTAL:.1f}s）")


# ============================================================ 文字
def _font(px: int, bold: bool = False) -> QFont:
    r"""字号一律用**像素**，不用点。

    点会跟着系统的 DPI 缩放走：这台机器上 `QFont(..., 52)` 画出来比 52 像素高
    得多，第一版的标题和副标题直接叠在一起了。画布是写死的 1280x720，字号就该
    是写死的像素 —— `setPixelSize` 是唯一跟屏幕设置无关的那个入口。
    """
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def block_h(p: QPainter, lines, gap: int) -> int:
    """一组文字排下来总共多高。用来把它整块居中。"""
    total = 0
    for i, (_, px, _, _) in enumerate(lines):
        p.setFont(_font(px))
        total += p.fontMetrics().height() + (gap if i else 0)
    return total


def draw_block(p: QPainter, y: int, lines, gap: int = 16,
               align=Qt.AlignHCenter, x: int = 0, width: int = W) -> int:
    """从 `y` 往下依次画几行，返回底边。`lines` 是 [(文字, 像素高, 颜色, 粗体)]。

    整体居中靠 `block_h` 先算总高、外面自己定起点 —— 比每行写死一个 y 稳，
    改字号不会撞上下一行。
    """
    for i, (s, px, color, bold) in enumerate(lines):
        if i:
            y += gap
        f = _font(px, bold)
        p.setFont(f)
        p.setPen(color)
        fm = p.fontMetrics()
        p.drawText(QRect(x, y, width, fm.height()), align | Qt.AlignTop, s)
        y += fm.height()
    return y


# ============================================================ 舞台
class Stage:
    """三个真控件 + 一条按帧号拨的时钟。

    控件只建一次，全程复用 —— 每帧新建一个 `PetWindow` 的话，立绘和精灵表要
    反复解码，一千多帧下来慢得没有意义。
    """

    def __init__(self):
        cfg = dict(DEMO_CFG)
        self.pet = PetWindow(cfg)
        self.pet.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.pet.show()
        self.pet.animator.stop()            # 停掉心跳，改由 _pet_at 手动拨

        self.toast = self.pet.toast
        self.toast.setAttribute(Qt.WA_DontShowOnScreen, True)

        self.chat = ChatWindow(cfg, voice=None)
        self.chat.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.chat.resize(CHAT_W, CHAT_H)
        self.chat.show()
        # 演示里聊天窗全程停在空记录状态（`_isolate` 保证读不到真记录），
        # 所以「一句都没聊过」的提示和三个快捷按钮本来就在。_chat_dirty 只在
        # 内容变了的时候重新抓图 —— 20 秒里大部分帧这张图是一模一样的。
        self._chat_dirty = True
        self._chat_pm = None

        self._typing_on = False             # 打字动画那三格现在开没开
        self._dance_on = False
        _pump(200)

    # ---------- 她 ----------
    def pet_at(self, t: float, *, cx: int, blink: bool = False,
               bounce: float = 1.0, dancing: bool = False,
               gaze: float = 0.0) -> QPixmap:
        r"""把她拨到 `t` 这一刻该有的样子，抓一张。

        手动拨的是 `PetAnimator` 的几个私有量，因为它的公开接口是**按真实时间
        走**的（`_tick` 里拿 `FPS_MS` 累加）。冒烟测试里也是这么干的，理由一样：
        25fps 的片子要的是确定的那一帧。

        - `t` 走的是动画器自己的时钟，管呼吸（周期 3.4s）。传全局时间就行，
          相位连续，不会两段之间跳一下。
        - `bounce` 是弹跳进度 0.0~1.0，1.0 = 没在弹。
        - `dancing` 为真时前几项全被忽略（见 anim.py 里「跳舞为什么是覆盖」），
          这时 `t` 改成**跳舞已经放了多久**。
        - `gaze` 是朝向 -1~+1。**必须显式给**：真机上它由鼠标位置算出来
          （`PetWindow._update_gaze`），而渲染的时候鼠标在哪跟片子毫无关系 ——
          不钉住的话，整支片子里她会一直歪在一个由录制者鼠标位置决定的角度上。
          同时直接写 `_gaze`（不光是目标值）：那是个带平滑的状态量，只设目标
          的话它要几帧才追上去，抓的是哪一帧就说不准了。
        """
        a = self.pet.animator
        if dancing:
            if not self._dance_on:
                self.pet.do_dance()         # 走公开入口，帧数/帧率/遍数照生产代码来
                self._dance_on = True
            a._dance_elapsed = t
        else:
            if self._dance_on:
                a._dance_frames = 0         # 收舞：state() 看到 0 就回到立绘
                a._dance_total = 0
                self._dance_on = False
            a._t = t
            a._bounce = bounce
            a._blinking = blink
            a._gaze = a._gaze_target = max(-1.0, min(1.0, gaze))
        return _grab(self.pet)

    def dance_len(self) -> float:
        """一遍舞有多长。素材是 20 帧 @15fps = 1.33 秒，但**不写死** —— 换了
        素材这儿跟着走，不然演示里会突然定格或者跳到一半。"""
        return (self.pet.dance_meta["frames"]
                / float(self.pet.dance_meta.get("fps", 15.0)))

    # ---------- 气泡 ----------
    def toast_on(self, speech: str, balance: str) -> None:
        """把气泡摆出来，并且**钉住不淡出**。

        `show_message` 会起一个 3.4 秒的 `_hold` 定时器，到点淡出。演示要的是
        它一直挂着，所以把两个定时器都掐了 —— 动画本身不是演示内容，真机上看
        一次就够，视频里挂着更好读。
        """
        self.toast.show_message(speech, balance)
        self.toast._hold.stop()
        self.toast._fade.stop()

    def toast_off(self) -> None:
        self.toast._hold.stop()
        self.toast._fade.stop()
        self.toast.hide()

    def toast_pos(self, pet_pm: QPixmap, pet_x: int, pet_y: int):
        """气泡摆在她头顶正中。用的是 `_relayout` 算好的尺寸，不是 `anchor_above`
        —— 那个吃的是**屏幕**坐标，而画布坐标跟屏幕没关系。"""
        self.toast._relayout()
        tx = pet_x + pet_pm.width() // 2 - self.toast.width() // 2
        ty = pet_y - self.toast.height() - 6
        return max(8, tx), max(8, ty)

    # ---------- 聊天窗 ----------
    def chat_touch(self) -> None:
        """告诉她「内容变了，下一帧重新抓图」。"""
        self._chat_dirty = True

    def chat_pixmap(self) -> QPixmap:
        if self._chat_dirty or self._chat_pm is None:
            # **弹 120 毫秒，不是一帧。** 一帧（30ms）不够：新消息插进来之后
            # 那个 holder 的行布局、气泡的 `sizeHint`（wordWrap 的 QLabel 要
            # 反算折行）和 `singleShot(0)` 的滚动是**串着**排的，30ms 只能走完
            # 头一两级。实测 30ms 下气泡停在半截尺寸上 —— 截图里她的气泡比该有的
            # 窄一截、长句干脆一个字都没长出来，看着像文本丢了。
            # 只在内容真的变了的时候才付这个钱（一整个聊天段落里大约变一百来次）。
            _pump(120)
            self._chat_pm = _grab(self.chat)
            self._chat_dirty = False
        return self._chat_pm


# ============================================================ 聊天脚本
def _chat_script():
    r"""聊天那一段的时序表，`[(本地秒, 事件), …]`。

    事件四种：

        ("say",  [(相对秒, "她说的话"), …])   她开一个新气泡，字一个个长出来
        ("you",  "我说的话")                  我发一条，立刻出现
        ("dots", None)                        她的气泡进入「…」打字态
        ("trace", "…")                        给**当前**那个气泡挂一行工具轨迹

    内容是照着一个真会话的形状编的：先问时间（逼出 `now`）、再让她记事（逼出
    `remember`）、最后一句纯闲聊。**两行轨迹都是真调出来的**，不是抄的文案 ——
    `agent.run` 返回什么就显示什么，改天它换了措辞这儿跟着换。
    """
    now_line = agent.run("now", None)
    remember_line = agent.run("remember", {"fact": "用户爱熬夜"})

    return [
        # 0~2 秒：空窗，欢迎语和三个快捷按钮都在（ChatWindow 自己画的，不用管）
        (2.0, ("you", "现在几点？")),
        (3.0, ("dots", None)),
        (4.2, ("trace", now_line)),
        (4.8, ("say", [(0.4, "都这个点了，别又熬到天亮哦。"),
                       (1.9, "塔菲在这儿陪着你喵。")])),
        # 「记住」这条走的是真工具，写进临时目录（见 _isolate）
        (7.8, ("you", "记住我爱熬夜")),
        (8.6, ("dots", None)),
        (9.4, ("trace", remember_line)),
        (10.0, ("say", [(0.5, "记住啦。"), (1.2, "到点塔菲就来催你睡觉喵。")])),
        (13.2, ("you", "今天有点累")),
        (14.0, ("say", [(0.6, "那就先把肩膀放松一下，"), (1.5, "别看屏幕啦。")])),
    ]


class ChatScene:
    """把上面那张表按帧喂进 `ChatWindow`。

    **不是重放录像，是重放操作**：每一帧的状态都是从「已经发生过哪些事件」推出来
    的，所以滚动、换行、气泡撑高、头像这些全是真控件自己算的。
    """

    def __init__(self, stage: Stage):
        self.st = stage
        self.script = _chat_script()
        self.done = 0                   # script 里已经执行到第几条
        self.bubble = None              # 当前那个「她的」气泡
        self.stream = None              # 正在长字的那一段：[(相对秒, 全文), …]
        self.stream_t0 = 0.0            # ^ 那一段是**哪一秒**开始长的，见 _grow
        self.pinned = False             # 气泡现在是不是停在「…」打字态

    def at(self, local: float) -> None:
        while self.done < len(self.script) and self.script[self.done][0] <= local:
            at, ev = self.script[self.done]
            self._run(at, ev)
            self.done += 1
        self._grow(local)

    def _run(self, at: float, ev) -> None:
        kind, payload = ev
        if kind == "you":
            self.st.chat._append_widget(payload, True)
            self.st.chat.suggests.setVisible(False)   # 聊起来了，快捷开场该收起来
            self.st.chat.history.append({"role": "user", "content": payload})
        elif kind == "dots":
            b = self._new_bubble()
            self._pin(b)
            self.stream = None
        elif kind == "trace":
            self.st.chat._add_trace(self.bubble, payload)
        elif kind == "say":
            self.stream = payload
            self.stream_t0 = at
            # 上一条是 dots 的话就接着用那个气泡（真程序里 `_on_tools` 也是
            # 这么做的：气泡一直挂着「…」，直到 `_on_chunk` 吐第一个字）。
            if self.st.chat.pending is None:
                self._pin(self._new_bubble())
            self.bubble = self.st.chat.pending
            self.st.chat.pending = None       # 打字态结束，但气泡还留着
        self.st.chat_touch()

    def _new_bubble(self):
        b = self.st.chat._append_widget("", False)
        b.traces = []
        self.st.chat.pending = b
        self.bubble = b
        return b

    def _pin(self, b) -> None:
        """让气泡停在「…」上。真程序里这是 `_typing_timer` 干的，
        演示里不需要动画，钉在第一格就够 —— 它要传达的信息是「她在忙」。"""
        b.set_text(TYPING, pin=True)
        self.pinned = True

    def _grow(self, local: float) -> None:
        r"""把正在长的那一段按时间切出可见的字数，再写进气泡。

        一个字一个字地长**只影响气泡里的文本**，不影响落盘 —— 演示里本来也没
        落盘（`chat_store.save` 是空的）。真程序那边这条不变式由 `_on_done` 管。

        **起点用的是 `stream_t0`（say 那条事件自己的时刻），不是「上一条已执行
        事件的时刻」**。第一版就是后者，于是后面每来一条新事件（比如下一句
        「你」）起点就往前挪、字数往回缩 —— 画面上她的字长到一半又退回去，
        看着像在抽搐。这个 bug 只在 t=28 那一帧的截图里露了个头。
        """
        if not self.stream or self.bubble is None:
            return
        text = ""
        for rel, full in self.stream:
            dt = local - (self.stream_t0 + rel)
            if dt <= 0:
                # 这一段还没轮到。后面更晚的段自然也还没轮到，直接停。
                break
            # 26 字/秒。比真机上的流式快一些 —— 真实速度取决于网络，视频里
            # 慢到能看清就行。
            text += full[:int(dt * 26)] if dt * 26 < len(full) else full
        if not text:
            # 一个字都还没到，继续停在「…」上，别留一个空白气泡。
            return
        if text != self.bubble.text() or self.pinned:
            self.bubble.set_text(text)
            self.pinned = False
            self.st.chat_touch()


# ============================================================ 一帧
def compose(st: Stage, scene: ChatScene, t: float) -> QImage:
    img = QImage(W, H, QImage.Format_RGB32)
    img.fill(WHITE)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    if t < T_TITLE[1]:
        _scene_title(p, t)
    elif t < T_IDLE[1]:
        _scene_idle(st, p, t)
    elif t < T_ALONE[1]:
        _scene_alone(st, p, t)
    elif t < T_TALK[1]:
        _scene_talk(st, p, t)
    elif t < T_DANCE[1]:
        _scene_dance(st, p, t)
    elif t < T_CHAT[1]:
        _scene_chat(st, scene, p, t)
    else:
        _scene_out(p, t)

    p.end()
    return img


def _blit_pet(st: Stage, p: QPainter, pm: QPixmap, cx: int) -> tuple:
    x = cx - pm.width() // 2
    y = PET_BOTTOM - pm.height()
    p.drawPixmap(x, y, pm)
    return x, y


def _fade(color: QColor, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def _scene_title(p: QPainter, t: float) -> None:
    # 淡入。开头 0.6 秒从空白里浮出来，比硬切干净。
    a = min(1.0, t / 0.6)
    lines = [
        ("塔菲桌宠", 78, _fade(INK, a), True),
        ("一只住在 Windows 桌面上的塔菲", 27, _fade(DIM, a), False),
        ("点她说话 · 双击聊天 · 右键更多", 24, _fade(PINK, a), False),
    ]
    gap = 20
    top = (H - block_h(p, lines, gap)) // 2 - 30
    draw_block(p, top, lines, gap)


def _scene_idle(st: Stage, p: QPainter, t: float) -> None:
    local = t - T_IDLE[0]
    # 眨眼安排在这一段里固定的一次，位置是掐着秒表挑的：太靠前会被上一段的
    # 淡出吃掉，太靠后又来不及在切镜头前看见。
    blink = 1.6 <= local <= 1.75
    # 朝鼠标转头，在这里演一遍：先左后右再回正。真机上这个值是鼠标位置算出来的
    # （`_update_gaze`），渲染时没有鼠标，所以手动扫一遍把它演出来 ——
    # 不演的话这一段跟「她只是一张静图」没区别。
    gaze = math.sin(local * 1.5)
    pm = st.pet_at(t, cx=PET_CX_SOLO, blink=blink, gaze=gaze)
    _blit_pet(st, p, pm, PET_CX_SOLO)
    draw_block(p, H - 62,
               [("她会跟着鼠标微微转头 · 脚下那片影子跟着弹跳一起缩", 24, DIM, False)])


def _scene_alone(st: Stage, p: QPainter, t: float) -> None:
    r"""没人理她，她自己动起来了。

    这一段是「她自己找事做」的实拍，**不是在演一个举手势的动画**：她在真实运行里
    被闲置计时器叫醒时，做的就是这两件事 —— 蹦一下 + 冒一句话。

    那句话是语音库里的**原句**（14.wav），不是编的。她说的时候头顶浮出那句话，
    是因为真机上就是这么做的：静音用户看不见声音，不写出来他只会看到
    「她莫名其妙弹了一下」。
    """
    local = t - T_ALONE[0]
    # 前 1.1 秒安静。她自己动之前本来就是安静的，一上来就动反而像被谁点了 ——
    # 而这一段要说的恰恰是「没人点她」。
    if local < 1.1:
        pm = st.pet_at(t, cx=PET_CX_SOLO)
    else:
        # 弹跳走**真实时长**（anim.py 的 BOUNCE_MS），不写死 0.62。
        # 改了关键帧曲线、片子跟着走，不用回来改这儿。
        span = animmod.BOUNCE_MS / 1000.0
        pm = st.pet_at(t, cx=PET_CX_SOLO,
                       bounce=min(1.0, (local - 1.1) / span))
    x, y = _blit_pet(st, p, pm, PET_CX_SOLO)

    if local >= 1.3:
        st.toast_on(ALONE_LINE, "")
        tx, ty = st.toast_pos(pm, x, y)
        p.drawPixmap(tx, ty, _grab(st.toast))
    else:
        st.toast_off()
    draw_block(p, H - 62,
               [("没人理她的时候，她自己会说话、蹦一下，或者跳个舞", 24, DIM, False)])


def _scene_talk(st: Stage, p: QPainter, t: float) -> None:
    local = t - T_TALK[0]
    # 点击那一下：弹跳进度 0 -> 1 走完一次（BOUNCE_MS=620ms），之后回待机
    bounce = min(1.0, local / 0.62) if local < 0.62 else 1.0
    pm = st.pet_at(t, cx=PET_CX_SOLO, bounce=bounce)
    x, y = _blit_pet(st, p, pm, PET_CX_SOLO)

    # 余额是点完才回来的（真实世界里要走一次 HTTP），所以气泡先只有话、
    # 0.7 秒后才补上数字 —— 跟 `set_balance` 的注释说的是同一回事。
    if local >= 0.35:
        if local >= 1.05:
            st.toast_on(TALK_SPEECH, TALK_BALANCE)
        else:
            st.toast_on(TALK_SPEECH, "余额查询中…")
        tx, ty = st.toast_pos(pm, x, y)
        p.drawPixmap(tx, ty, _grab(st.toast))
    draw_block(p, H - 62, [("点她一下：说句话、报个余额、顺便蹦一下", 24, DIM, False)])


def _scene_dance(st: Stage, p: QPainter, t: float) -> None:
    st.toast_off()
    local = t - T_DANCE[0]
    one = st.dance_len()
    # 三段：起手 0.4 秒静止，然后跳两遍，最后 0.5 秒静止收尾。两遍不是三遍 ——
    # 生产代码点一次跳三遍，但视频里第三遍已经不再提供新信息了。
    d = local - 0.4
    if d < 0:
        pm = st.pet_at(t, cx=PET_CX_SOLO)
    elif d < one * 2:
        pm = st.pet_at(d, cx=PET_CX_SOLO, dancing=True)
    else:
        pm = st.pet_at(t, cx=PET_CX_SOLO)
    _blit_pet(st, p, pm, PET_CX_SOLO)
    draw_block(p, H - 62,
               [("她自己会跳 · 想立刻看就右键 →「跳个舞」", 24, DIM, False)])


def _scene_chat(st: Stage, scene: ChatScene, p: QPainter, t: float) -> None:
    scene.at(t - T_CHAT[0])
    pm = st.pet_at(t, cx=PET_CX_CHAT)
    _blit_pet(st, p, pm, PET_CX_CHAT)

    shot = st.chat_pixmap()
    p.drawPixmap(CHAT_X, CHAT_Y, shot)
    # 窗口自己在白底上也几乎是白的，给它描一圈边，不然看不出边界在哪
    p.setPen(QColor(238, 214, 222))
    p.drawRect(CHAT_X, CHAT_Y, shot.width() - 1, shot.height() - 1)

    # 左边那句跟着她的宽度居中，跟她的脚对齐 —— 两行字各自贴着自己那一半。
    draw_block(p, H - 58, [("双击她就能聊 · 回车发送", 24, DIM, False)],
               x=0, width=PET_CX_CHAT * 2)
    draw_block(p, H - 58, [("她的话一个字一个字蹦出来", 24, DIM, False)],
               x=PET_CX_CHAT * 2, width=W - PET_CX_CHAT * 2)


def _scene_out(p: QPainter, t: float) -> None:
    local = t - T_OUT[0]
    a = min(1.0, local / 0.5)
    lines = [
        ("塔菲桌宠", 62, _fade(INK, a), True),
        ("github.com/Cheng1520/taffy-pet", 27, _fade(DIM, a), False),
        ("非官方同人作品 · 仅供娱乐 · 代码以 MIT 发布", 22, _fade(DIM, a), False),
    ]
    gap = 20
    top = (H - block_h(p, lines, gap)) // 2
    draw_block(p, top, lines, gap)


# ============================================================ 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--still", type=float, default=None,
                    help="只出这一秒的一张 PNG，用来对着看排版")
    args = ap.parse_args()

    _isolate()
    # 关掉 HiDPI 缩放：视频是固定的 1280x720，不该跟着这台机器的缩放比例变。
    # **必须在 QApplication 之前**。
    QApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)
    app = QApplication([sys.argv[0]])          # noqa: F841  建控件要，得接住引用

    out_dir = ROOT / "_work" / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = "still" if args.still is not None else "frame"

    st = Stage()
    scene = ChatScene(st)

    if args.still is not None:
        # 先把聊天那一段**逐帧重放**到目标时刻，再画这一张。直接跳到第 30 秒的话
        # 中间那些帧的状态从来没发生过 —— 上一句话的字还没长出来，下一条气泡
        # 就已经接管了，出图跟视频里同一秒对不上。
        if args.still > T_CHAT[0]:
            i = int(T_CHAT[0] * FPS)
            while i * SPF < args.still:
                scene.at(i * SPF - T_CHAT[0])
                i += 1
        img = compose(st, scene, args.still)
        p = out_dir / f"{prefix}-{args.still:05.1f}.png"
        img.save(str(p))
        print(f"[demo] {p}")
        return 0

    # 音效落在哪一秒。跟她动作对齐：点她之后 0.35 秒出声（跟气泡同时），
    # 跳舞那段配一句，聊天里两条轨迹各配一句。
    clips = [
        (T_ALONE[0] + 1.3, "14.wav", 0.85),        # 「塔菲在看着你哦。」—— 她自己说的
        (T_TALK[0] + 0.35, "01.wav", 0.85),        # 「在呢在呢，怎么啦喵。」
        (T_DANCE[0] + 0.9, "03.wav", 0.85),        # 「雏草姬今天也要开心哦。」
        (T_CHAT[0] + 10.6, "18.wav", 0.85),        # 「塔菲记得的。」—— 记事那条之后
        (T_CHAT[0] + 15.2, "13.wav", 0.85),        # 「累了就早点休息喵。」
    ]
    wav = out_dir / "taffy-demo.wav"
    build_audio(clips, wav)

    n = int(round(TOTAL * FPS))
    mp4 = out_dir / "taffy-demo.mp4"

    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ff, "-y", "-loglevel", "warning",
           "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-",
           "-i", str(wav),
           "-c:v", "libx264", "-preset", "slow", "-crf", "18",
           "-pix_fmt", "yuv420p",
           # 24kHz 单声道，码率再高也是白给：160k 会让编码器每帧超上限然后
           # 自己 clamp 回去（日志里那行 "Too many bits ... clamping"）。
           "-c:a", "aac", "-b:a", "64k",
           "-shortest", "-movflags", "+faststart",
           str(mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    assert proc.stdin is not None

    try:
        for i in range(n):
            t = i * SPF
            img = compose(st, scene, t)
            buf = img.constBits()
            buf.setsize(img.bytesPerLine() * img.height())
            proc.stdin.write(bytes(buf))
            if i % (FPS * 5) == 0:
                print(f"[demo] {t:5.1f}s / {TOTAL:.1f}s")
    finally:
        proc.stdin.close()
        rc = proc.wait()

    if rc != 0:
        print(f"[demo] ffmpeg 退出码 {rc}")
        return rc
    print(f"[demo] 好了：{mp4}（{mp4.stat().st_size / 1e6:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
