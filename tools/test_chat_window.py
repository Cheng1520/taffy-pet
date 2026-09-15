"""聊天窗口的冒烟测试：能发、能收、能流式、能停、能关，以及好不好看里能验的那几条。

    PYTHONUTF8=1 python tools/test_chat_window.py        # 跑测试
    PYTHONUTF8=1 python tools/test_chat_window.py --shot # 生成目检图 assets/_chat.png

Task 5 的手动验证（真填一个 Key、发一句「你好」、肉眼看打字机效果和四条边界）
既没有 Key 也没人坐在 GUI 前面，做不了。这个文件把那几条换成无头断言：起一个
只回 SSE 的本地假接口（直接复用 tools/test_chat.py 那套），用 offscreen 平台建
真的 ChatWindow，把「发出去的消息落盘了没、流式的字拼全了没、点停止留没留住
已经吐出来的半句、流式中关窗会不会崩、重开窗口历史回没回来、右键菜单连点两次
会不会开出两个窗口」全部钉死。

Task 6 加了视觉，但视觉里只有一半能自动验（气泡真画出来了、两边底色、75% 宽度
上限、头像框贴合立绘）。「好不好看」本身验不了，也不假装验过 —— 那几条留给用户，
`--shot` 只负责把图生成好放在那儿。**图里不会有标题栏**（grab() 只抓客户区），
标题栏单独在 case_titlebar 里验，它换到原生平台的子进程去跑。

跑测试的主进程钉在 offscreen 上，所以 `[chat] 标题栏上色失败…HRESULT=0x80070006`
那行日志是**正常的**：offscreen 没有原生窗口，DWM 当然拒绝。产品代码在那里就是
该打印 + 返回 False（不打印才是缺陷），别把它当成失败。

**路径隔离（这条最要紧）**：非打包运行时 paths.DATA_DIR 就是仓库根目录，也就是说
仓库根下的 config.json 是用户真实的配置文件（里面有他的 DeepSeek API Key）、
chat.json 是他真实的聊天记录。而 ChatWindow.closeEvent 会调 cfgmod.save()、
聊天记录每次发送都会 chat_store.save() —— 测试里跑一遍真窗口就会把用户的东西
覆盖掉。所以建窗口之前必须先把这两个模块的路径常量指到临时目录去。
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# 控制台是 GBK 时打印中文会抛 UnicodeEncodeError，被用例接住后报成假失败。
# 把这个进程的 stdout 掰成 UTF-8，别要求用户记得加 PYTHONUTF8=1。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

# 必须在任何 PyQt5 的 import 之前 —— 没有显示器也要能建窗口
os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# 假接口那套直接用 test_chat 的：它把 main() 藏在 __main__ 后面，import 没有副作用，
# 顺便还能白拿它模块级设的 NO_PROXY（绕开本机系统代理对 127.0.0.1 的劫持）。
import test_chat as TC                                  # noqa: E402

from PyQt5.QtCore import Qt, QEvent, QEventLoop, QPointF, QTimer   # noqa: E402
from PyQt5.QtGui import QMouseEvent                     # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox   # noqa: E402

from taffy_pet import chat as CH                        # noqa: E402
from taffy_pet import chat_store                        # noqa: E402
from taffy_pet import chat_window as CW                 # noqa: E402
from taffy_pet import config as cfgmod                  # noqa: E402
from taffy_pet import memory                            # noqa: E402
from taffy_pet import paths                             # noqa: E402

FULL = "".join(TC.PIECES)          # 假接口会吐的完整回复

# 断言借 test_chat 那份账本（它自己的 main 不会跑，所以里面只会有我们的失败），
# 打印格式和别的手写测试保持一致。
ck = TC.ck
FAILS = TC.FAILS


# ---------- 小工具 ----------
def _isolate_paths(tmp: str) -> None:
    """把所有会落到仓库根的路径都指到临时目录。见文件头的说明，这一步不做就会动用户的真数据。

    四个都要补，别只补前两个：`paths.DATA_DIR` 是 `ensure_data_dir()` 建目录用的，
    `paths.PERSONA_PATH` 是「编辑人设」要写的那份 —— 现在没有用例调 `edit_persona()`，
    但谁哪天加一条，只补了 CONFIG_PATH/CHAT_PATH 的话就会往仓库根写 `persona.md`。
    `CW.PERSONA_PATH` 是 chat_window 里 `from .paths import ...` 按值引进来的那份副本，
    改 paths 模块的属性影响不到它，得单独改。
    """
    d = Path(tmp)
    paths.DATA_DIR = d
    paths.PERSONA_PATH = d / "persona.md"
    cfgmod.CONFIG_PATH = d / "config.json"
    chat_store.CHAT_PATH = d / "chat.json"
    CW.PERSONA_PATH = d / "persona.md"
    # 记忆文件也要隔离，而且它是最容易漏的一个：她的「记住」工具会在**对话过程中**
    # 自动落盘，用例不需要显式写文件就会命中 —— 漏掉的话跑一次智能体用例
    # 就往用户真实的 memory.md 里塞一条测试用的假记忆。
    memory.MEMORY_PATH = d / "memory.md"
    print(f"  数据落盘隔离到 {tmp}")


def pump(ms: int) -> None:
    """把事件循环转起来 ms 毫秒。信号是队列连接，槽要靠这个才会跑。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


def wait_until(pred, timeout_ms: int = 8000, step_ms: int = 20) -> bool:
    """反复泵事件循环直到 pred() 为真。不能用 time.sleep —— 那样槽永远不跑。"""
    waited = 0
    while waited <= timeout_ms:
        if pred():
            return True
        pump(step_ms)
        waited += step_ms
    return bool(pred())


def texts(win) -> list:
    """按顺序读出消息区里每个气泡/提示的文字（末尾那个弹簧没有控件，跳过）。

    Task 6 之后气泡外面多了一层装头像的行（`_Bubble._outer`），插进 `msgs` 的是
    那一行而不是气泡本身，所以这里得往下找一趟 —— 直接 `w.text()` 拿到的是
    「QWidget 没有 text」的异常。

    找不到气泡的行**故意**不兜底：那正是「半句气泡没删干净、空壳留在消息区里」
    的样子（`_drop_pending` 删错控件时就是这样），让它原样炸出来比吞掉强。
    """
    out = []
    for i in range(win.msgs.count()):
        w = win.msgs.itemAt(i).widget()
        if w is None:
            continue
        # 日期分隔线不在这套口径里 —— 它是消息区第三类控件，另有 case_day_separator 管。
        # 不排掉的话，「今天」会被当成一条气泡，所有数数的断言都会平移一格。
        if w.objectName() == CW.DAY_OBJECT:
            continue
        bubble = w if isinstance(w, CW._Bubble) else w.findChild(CW._Bubble)
        out.append(bubble.text() if bubble is not None else w.text())
    return out


def rows(win) -> int:
    """消息区里有几个顶层控件（末尾那个弹簧不算）。

    跟 texts() 分开：texts() 只读得出**有文字**的控件，而行数看的是「到底插进去了
    几个顶层控件」。Task 6 之后一个气泡外面还套着一层装头像的行，`_drop_pending`
    靠 `bubble._outer` 删对那个 —— 删错的话留下来的正是那个**行**，它自己没有文字，
    只有数行数才看得见（界面上的表现是消息区里多出一块空白）。
    """
    return sum(1 for i in range(win.msgs.count())
               if (w := win.msgs.itemAt(i).widget()) is not None
               and w.objectName() != CW.DAY_OBJECT)


def pixel(widget, x: int, y: int) -> int:
    """控件自己抓一张图，取 (x, y) 那点的颜色。"""
    return widget.grab().toImage().pixel(x, y)


def reset_handler() -> None:
    """每个用例之前必须重置 —— 这些是类属性，上一个用例改过的会留到下一个。"""
    TC.Handler.status = 200
    TC.Handler.gate = None
    TC.Handler.delay = 0.0
    TC.Handler.no_final_newline = False
    TC.Handler.seen = {}


_OPEN_WINDOWS = []      # new_window() 建出来的窗口，run_case 收尾时统一收掉


def _reap_windows() -> None:
    """把这个用例留下来的聊天窗收干净：close + 等线程。

    不这么做的话，「用例自己断言失败 → 提前 return → 留下一个 worker 还在跑的
    窗口」会让下一个用例的 QApplication 活动把它带走：窗口析构 → QThread 析构时
    线程还在跑 → Qt 直接 qFatal → abort(0xC0000409)。那是**进程级**的，Python 里
    接不住，表现是整条套件无声退出、一条 FAIL 都留不下来（实测：让 chat_store.save
    不落盘那个变异体的退出码是 3221226505，后面所有用例一个都没跑，人也只看到一个
    裸的错误码）。诊断信息比失败本身重要，所以每个用例都做这一步。

    登记表不是为了好看：用例里那个局部变量 `win` 出了函数就没引用了，窗口可能
    在这一步之前就已经析构，那时再去遍历 topLevelWidgets 已经找不到它 —— 先攥着
    引用、再收，才有得收。
    """
    windows = list(_OPEN_WINDOWS)
    for w in QApplication.topLevelWidgets():        # 绕过 new_window 建的也一并收
        if isinstance(w, CW.ChatWindow) and w not in windows:
            windows.append(w)
    for w in windows:
        try:
            w.close()                               # closeEvent → _stop_worker
        except Exception:                           # noqa: BLE001  收尾不能再抛出去
            import traceback
            traceback.print_exc()
        worker = getattr(w, "worker", None)
        if worker is not None:
            worker.wait(3000)
    _OPEN_WINDOWS.clear()


def run_case(fn) -> None:
    """一个用例崩了别把后面的都带下水：记一笔，重置假接口，把窗口收掉，接着跑。"""
    reset_handler()                 # 上一个用例改过的类属性不能留到这一个
    try:
        fn()
    except Exception as e:                              # noqa: BLE001
        import traceback
        traceback.print_exc()
        ck(f"{fn.__name__} 不该抛异常", f"{type(e).__name__}: {e}", None)
    finally:
        reset_handler()
        _reap_windows()


def new_window(cfg: dict):
    win = CW.ChatWindow(cfg)
    _OPEN_WINDOWS.append(win)
    return win


def new_pet():
    """建一只塔菲主窗口，建不起来就记一笔失败、返回 None（调用方 `if pet is None: return`）。

    缺素材时 PetWindow 抛的是 SystemExit，它继承 BaseException 而不是 Exception ——
    只接 Exception 的话整个测试进程会当场退出，后面所有用例连跑都跑不到。
    用例本来也不多，这儿直接把「建不起来」记成失败，不搞 skip 那一套。
    """
    from taffy_pet.pet import PetWindow                # 只有主窗口这几个用例要它
    try:
        return PetWindow(cfgmod.load())
    except BaseException as e:                          # noqa: BLE001
        ck("塔菲主窗口建得起来", f"{type(e).__name__}: {e}", None)
        return None


# ---------- 用例 ----------
def case_no_key() -> None:
    """没 Key：不联网，给一句人话提示，不能留下一个空气泡。"""
    print("没 Key 时发消息：")
    chat_store.clear()
    TC.Handler.seen = {}
    win = new_window({"api_key": ""})
    win.input.setPlainText("在吗")
    win.send()
    ck("这一轮立刻收尾了", wait_until(lambda: win.worker is None), True)
    # 先数行数再看文字：Task 6 之后气泡外面套着一层装头像的行，_drop_pending 删错控件
    # 的话留下的就是那个没有文字的行 —— 只有行数看得见它，而且它会把下面的 texts()
    # 直接炸掉，先在这儿点名比看一句 AttributeError 强。
    ck("消息区就三行（欢迎语+用户行+提示），半句那一行整行删掉了", rows(win), 3)
    ck("给了「还没设置 API Key」的提示",
       any("还没设置 API Key" in t for t in texts(win)), True)
    ck("空的那个半句气泡清掉了", win.pending, None)
    # 光查 pending is None 不够：只把引用置 None、控件不真从布局里摘掉，这条照样绿
    # （界面上会留一个空气泡）。这里查的是它真的不在了 —— 欢迎语 + 用户气泡 + 提示，三条。
    ck("消息区就是欢迎语+用户气泡+提示三条", len(texts(win)), 3)
    ck("里面没有空气泡", [t for t in texts(win) if not t.strip()], [])
    ck("一个字都没往接口发", TC.Handler.seen, {})
    ck("落盘里只有 user 那条", [m["role"] for m in chat_store.load()], ["user"])
    win.close()


def case_send_then_stream() -> None:
    """能发、能收：用户气泡 + 落盘，然后流式的字一个字不差地拼完。"""
    print("发送：")
    chat_store.clear()
    win = new_window({"api_key": "sk-test"})
    win.input.setPlainText("在吗")
    win.send()
    pend = win.pending                       # 流式填充的那个气泡

    # send() 是同步的，信号又是队列连接 —— 这里事件循环还没转，所以看到的一定是
    # 「刚发完、回复还没回来」的状态，不用等。
    ck("用户气泡出现了", "在吗" in texts(win), True)
    hist = chat_store.load()
    ck("落盘末尾就是这条 user",
       (hist[-1]["role"], hist[-1]["content"]), ("user", "在吗"))
    ck("按钮变成「停止」", win.btn.text(), "停止")

    print("流式回复：")
    ck("回复收尾了", wait_until(lambda: win.worker is None), True)
    ck("气泡最终文本是完整回复", pend.text(), FULL)
    ck("攒下来的增量跟气泡一致", win.pending_text, FULL)
    ck("收完 pending 归位", win.pending, None)
    ck("按钮变回「发送」", win.btn.text(), "发送")
    hist = chat_store.load()
    ck("落盘末尾是 assistant 且内容相同",
       (hist[-1]["role"], hist[-1]["content"]), ("assistant", FULL))
    ck("内存里的历史也一致", win.history[-1]["content"], FULL)
    ck("请求打到了假接口并带上 Key", TC.Handler.seen.get("auth"), "Bearer sk-test")
    win.close()


def _text_body(*pieces: str) -> bytes:
    """一串正文增量 + [DONE]。跟假接口平时的模板同一种形状。"""
    out = b""
    for p in pieces:
        out += TC._sse(p)
    return out + b"data: [DONE]\n\n"


def case_agent_tool_round() -> None:
    r"""她调工具那一轮：接口要调工具 → 真的执行 → 结果回填 → 才轮到回复。

    这条**故意走真网络路径**（假接口 + 真 ChatWorker + 真 ChatWindow），
    而不是直接戳 `_on_tools`。要验的东西全在协议形状上：
    - 第二轮请求里必须带着第一轮那个 `assistant` 的 `tool_calls`，
      以及 `tool_call_id` 对得上的 `tool` 结果。少了任何一半，接口都会 400，
      或者模型根本不知道工具跑过、照着空气回答。
    - 工具真的执行了（记忆文件里落下了那条），不是只把调用转了一圈。
    - 界面上只能有**一个**气泡收尾 —— 中途那一轮她已经吐的字（或者占位符）
      必须被顶掉，不然「让我想想…」会和后半句拼在同一个气泡里。
    """
    print("智能体工具轮：")
    chat_store.clear()
    tool_body = TC.tool_sse(
        # 形状照抄真接口：name/id 在第一块，arguments 后来一段段拼
        {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                         "function": {"name": "remember", "arguments": ""}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": '{"fact":'}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": ' "用户爱熬夜"}'}}]},
    )
    TC.Handler.seen_all = []
    TC.Handler.body_sequence = [tool_body, _text_body("好", "的喵，", "记下了")]

    win = new_window({"api_key": "sk-test"})
    win.input.setPlainText("记住我爱熬夜")
    win.send()
    pend = win.pending

    # 两个收尾信号互斥的后果就在这儿：第一轮不发 done，所以 pending 只有在
    # 第二轮真的答完之后才会归 None。中途卡住的话这里会一直等到超时。
    ck("整轮结束了（两轮都跑完）", wait_until(lambda: win.pending is None), True)
    TC.Handler.body_sequence = None

    ck("工具真的执行了", memory.load(), ["用户爱熬夜"])
    ck("气泡里是第二轮的正文", pend.text(), "好的喵，记下了")
    ck("界面只有一个气泡在收尾", win.btn.text(), "发送")

    reqs = [r.get("json") or {} for r in TC.Handler.seen_all]
    ck("一共发了两次请求", len(reqs), 2)

    first, second = reqs[0], reqs[1]
    ck("第一轮带上了工具说明", bool(first.get("tools")), True)
    ck("第一轮还不是 tool 结果", [m["role"] for m in first["messages"]][-1], "user")

    roles = [m["role"] for m in second["messages"]]
    ck("第二轮末尾是 tool 结果", roles[-1], "tool")
    ck("第二轮末尾往前是 assistant 的调用", roles[-2], "assistant")

    call = second["messages"][-2].get("tool_calls") or [{}]
    ck("回填的调用带上了 id", call[0].get("id"), "call_1")
    ck("回填的调用带上了名字",
       (call[0].get("function") or {}).get("name"), "remember")
    ck("回填的调用带上了完整参数",
       (call[0].get("function") or {}).get("arguments"), '{"fact": "用户爱熬夜"}')
    ck("tool 结果的 tool_call_id 对得上",
       second["messages"][-1].get("tool_call_id"), "call_1")

    # 协议脚手架是**本轮临时**的，不该被写进聊天记录 —— 下次开窗回填历史时
    # 那些 tool_call_id 没有任何意义，读起来也莫名其妙。
    ck("聊天记录里没有 tool 角色",
       [m["role"] for m in chat_store.load()], ["user", "assistant"])
    win.close()


def case_agent_off() -> None:
    """关掉智能体之后，请求里不该再出现 tools —— 那是这个开关唯一的作用。"""
    print("关掉智能体的开关：")
    chat_store.clear()
    TC.Handler.seen_all = []
    win = new_window({"api_key": "sk-test", "agent": False})
    win.input.setPlainText("在吗")
    win.send()
    ck("回复照样收尾", wait_until(lambda: win.worker is None), True)
    ck("请求里没有 tools",
       "tools" in ((TC.Handler.seen_all[0].get("json") or {})), False)
    ck("回复内容没受影响", win.history[-1]["content"], FULL)
    win.close()


def case_stop_mid_stream() -> None:
    """流式中点「停止」：已吐出来的部分留着，并且以 assistant 落盘。"""
    print("流式中点停止：")
    chat_store.clear()
    TC.Handler.delay = 0.05        # 慢慢吐，保证按下去的时候还没吐完
    TC.Handler.gate = threading.Event()
    gate = TC.Handler.gate

    win = new_window({"api_key": "sk-test"})
    win.input.setPlainText("在吗")
    win.send()
    pend = win.pending

    # gate 让服务端发完第一行就卡住 —— 这是唯一能确定「此刻正停在流式中」的办法
    ck("流没结束就收到了首块", wait_until(lambda: bool(win.pending_text)), True)
    ck("这时候离吐完还早", win.pending_text != FULL, True)
    # 这条必须在停止**之前**查，而且必须查「流式进行中」的气泡。收尾之后 _on_done 会用
    # 完整文本再写一遍气泡，两条路径互为备份 —— 那时候再查，把 _on_chunk 里那句 set_text
    # 整个删掉（也就是把打字机效果删掉）都测不出来。此刻还没收尾，只有 _on_chunk 能让它对上。
    ck("流式中气泡已经显示到首块", pend.text(), win.pending_text)

    win.btn.click()                # 用户点停止
    gate.set()                     # 放行服务端，让这一轮能正常收尾

    ck("这一轮收尾了", wait_until(lambda: win.worker is None), True)
    part = pend.text()
    ck("留下的是非空的部分文本", bool(part) and part != FULL, True)
    ck("这段确实是完整回复的前缀", FULL.startswith(part), True)
    ck("收尾后 pending 归位", win.pending, None)
    # 停止这条路的语义是「已吐出的字留着」，所以上面 pending 归位还不够，得真查那段
    # 部分文本还在消息区里 —— 走的是和断流/关窗相反的那条路。
    ck("半句气泡还留在消息区", part in texts(win), True)
    ck("落盘的是 assistant", win.history[-1]["role"], "assistant")
    hist = chat_store.load()
    ck("落盘内容就是这段部分文本",
       (hist[-1]["role"], hist[-1]["content"]), ("assistant", part))
    ck("攒的增量和气泡一致", win.pending_text, part)
    win.close()


def case_close_mid_stream() -> None:
    """流式中直接关窗：不崩，线程收干净，半句不落盘。"""
    print("流式中直接关窗：")
    chat_store.clear()
    TC.Handler.delay = 0.05
    TC.Handler.gate = threading.Event()
    gate = TC.Handler.gate

    win = new_window({"api_key": "sk-test"})
    win.input.setPlainText("在吗")
    win.send()
    ck("流没结束就收到了首块", wait_until(lambda: bool(win.pending_text)), True)

    # closeEvent 里的 worker.wait(3000) 会把主线程堵死，这期间 QTimer 根本不会触发，
    # 所以放行服务端必须从另一个线程来 —— 不然要白等 3 秒、走到 terminate() 那条
    # 路上去，测到的就不是「正常收尾」了。
    threading.Timer(0.3, gate.set).start()
    # 顺手记一笔 cfgmod.save 有没有在关窗时被调到。「关窗时试图保存」这件事本身该
    # 有人守着 —— 不然哪天 closeEvent 里那行被删了没人知道。
    # （Task 6 时这儿的说法是「几何存下去也读不回来，那是已裁决的偏差」：当时
    # config.load() 会把不在 DEFAULTS 里的键丢掉，几何确实记不住。Task 7 把
    # chat_geometry 加进了 DEFAULTS，这句话就不成立了 —— 几何现在读得回来，
    # 往返那条由 case_config_geometry_roundtrip 守着。）
    saved = []
    real_save = cfgmod.save
    cfgmod.save = lambda cfg: saved.append(dict(cfg))
    try:
        win.close()
        crashed = False
    except Exception as e:                              # noqa: BLE001
        crashed = f"{type(e).__name__}: {e}"
    finally:
        cfgmod.save = real_save
    ck("关窗没崩", crashed, False)
    ck("关窗时把 config 存了一笔", len(saved), 1)
    ck("存的那份带上了 chat_geometry", "chat_geometry" in saved[0], True)
    ck("worker 收干净了", win.worker, None)
    # 关窗也要把那个半句气泡收掉。它从没写进 chat.json，留在控件里的话：close() 只是
    # 隐藏，同一个窗口再 show 出来它还挂在那儿 —— 界面看得见、盘里没有、重启就消失。
    # 这条和 case_stop_mid_stream 的「半句留着」是**相反**的两条路，两条都得守着。
    ck("关窗后半句气泡也清掉了", win.pending, None)
    ck("它没留在消息区里", [t for t in texts(win) if not t.strip()], [])

    # 关完必须再把事件循环转一会儿。worker.wait() 是不处理事件的，那一刻 done 就算
    # 被 emit 了也只是个躺在队列里的信号；这里不转的话「断开没断开」根本测不出来，
    # 断言会变成一句永远正确的话。
    pump(300)
    # _stop_worker 先 disconnect() 再 wait，所以中途断流那次 done 不会回调到窗口上，
    # 那半句自然也不会落盘 —— 界面上它被丢了，落盘里也不该有。反过来说，断开要是
    # 没了，这里就会看到一条凭空多出来的 assistant。
    ck("只有那条 user，没有 assistant",
       [m["role"] for m in chat_store.load()], ["user"])

    # brief Step 3 第 3 条的另一半：close() 只是把窗口藏起来，同一个对象还能 show 回来。
    # 重开之后消息区必须和落盘对得上 —— 多出任何一条盘里没有的东西就是幽灵气泡。
    # 比的是**整份**而不是尾巴：只比尾巴的话，将来中间多插一条也照样绿。欢迎语是个
    # 例外（它本来就不落盘），排掉它，剩下的必须一条不多一条不少地等于落盘那些。
    welcome = "和 taffy 打个招呼吧喵"
    win.show()
    pump(50)
    shown = [t for t in texts(win) if t.strip() and t != welcome]
    ck("重开同一个窗口，消息区就是落盘的那些",
       shown, [m["content"] for m in chat_store.load()])
    win.close()


def case_reopen_backfills() -> None:
    """关掉再开：之前存的对话要回到消息区里。"""
    print("重开窗口回填历史：")
    chat_store.clear()
    chat_store.save([chat_store.make("user", "之前说的"),
                     chat_store.make("assistant", "记得喵")])
    win = new_window({})
    ck("history 跟落盘一致", win.history, chat_store.load())
    ck("消息区按顺序回填了", [t for t in texts(win) if t], ["之前说的", "记得喵"])
    win.close()


def case_clear_history() -> None:
    """清空：确认框点「是」之后，盘里和界面里都得干净。"""
    print("清空对话记录：")
    chat_store.save([chat_store.make("user", "在吗"),
                     chat_store.make("assistant", "在呢喵")])
    win = new_window({"api_key": "sk-test"})    # 下半场要在流式中清空，得有 Key
    ck("清空前有两条", len(win.history), 2)

    # 弹窗会阻塞，只能把 question 临时换掉。用完必须换回来，不然后面的用例
    # 万一真弹个框就挂住了。
    real_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        win.clear_history()
    finally:
        QMessageBox.question = real_question

    ck("盘里清光了", chat_store.load(), [])
    ck("内存里的历史也空了", win.history, [])
    ck("消息区只剩一句提示", [t for t in texts(win) if t], ["清空了喵，重新开始"])
    # 光看文字不够：那个循环的终止条件一旦写成 > 0，会把末尾的弹簧一起删掉，
    # 而 _append_notice 的 insertWidget(-1) 恰好又把提示补在末尾，文字顺序看不出区别。
    # 直接查布局：末尾必须是弹簧（没有控件），而且总共就弹簧 + 提示两项。
    ck("末尾的弹簧还在", win.msgs.itemAt(win.msgs.count() - 1).widget(), None)
    ck("消息区就是弹簧 + 一句提示", win.msgs.count(), 2)

    # 流式中清空：控件会被整片删掉，所以 pending 必须跟着归位，否则它就成了指向
    # 已删控件的悬垂引用（Task 6 一碰就崩），pending_text 也会留着上一轮的残字。
    TC.Handler.delay = 0.05
    TC.Handler.gate = threading.Event()
    gate = TC.Handler.gate
    win.input.setPlainText("在吗")
    win.send()
    win.input.clear()
    ck("又流起来了", wait_until(lambda: bool(win.pending_text)), True)
    threading.Timer(0.3, gate.set).start()      # 同上：wait(3000) 期间 QTimer 不会触发
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        win.clear_history()
    finally:
        QMessageBox.question = real_question
    # clear_history 要是在这儿抛了，run_case 会记一笔，不会静默过去
    ck("半句气泡的引用归位了", win.pending, None)
    ck("攒的残字也清了", win.pending_text, "")
    ck("盘里还是空的", chat_store.load(), [])
    win.close()


def case_two_sends_same_window() -> None:
    """同一个窗口连发两条：一轮一轮互不串味，正在回复时点发送不能再发一条。

    之前每个用例都新建窗口，于是「上一轮的残留带进下一轮」「正在回复时又发一条」
    这两类问题一次都测不到。
    """
    print("同一窗口连发两条：")
    chat_store.clear()
    win = new_window({"api_key": "sk-test"})

    win.input.setPlainText("第一条")
    win.send()
    ck("第一条收尾了", wait_until(lambda: win.worker is None), True)
    ck("第一条的气泡是完整回复", win.history[-1]["content"], FULL)

    # 第二条用 gate 卡在流式中，好查「流式进行中」的状态
    TC.Handler.delay = 0.05
    TC.Handler.gate = threading.Event()
    gate = TC.Handler.gate
    win.input.setPlainText("第二条")
    win.send()
    pend2 = win.pending
    # 这条必须紧接着 send() 同步查：此刻还一个 chunk 都没到，攒的增量只能是空的。
    # 上一轮的残字要是没清干净（_start_reply 漏了清零），这里立刻就是 FULL。
    ck("新一轮的增量从零开始攒", win.pending_text, "")
    # 欢迎语 / 第一条 / 第一条的回复 / 第二条，再加她那个还没吐字的占位气泡
    # —— 上一轮的气泡没被顶掉。占位符是 CW.TYPING（「…」）：`_start_reply` 先把
    # 气泡建出来，此刻首块还没到，所以它显示的是占位符而不是空串。
    ck("上一轮的气泡还在，第二条也出来了",
       [t for t in texts(win) if t.strip()],
       ["和 taffy 打个招呼吧喵", "第一条", FULL, "第二条", CW.TYPING])

    ck("第二条流没结束就收到了首块", wait_until(lambda: bool(win.pending_text)), True)
    ck("第二条流式中气泡显示到首块", pend2.text(), win.pending_text)
    ck("第二条是完整回复的前缀", FULL.startswith(win.pending_text), True)

    # 正在回复时再点「发送」：既不能真发出去，也不能把输入框里的字吃掉
    win.input.setPlainText("第三条")
    win.send()
    ck("正在回复时再发不会进历史", len(win.history), 3)
    ck("输入框里的字没被吃掉", win.input.toPlainText(), "第三条")

    gate.set()
    ck("第二条也收尾了", wait_until(lambda: win.worker is None), True)
    ck("第二条的气泡最终是完整回复", pend2.text(), FULL)
    ck("第二条攒下来的增量也对", win.pending_text, FULL)
    ck("两条各落各的，中间没有串味",
       [(m["role"], m["content"]) for m in chat_store.load()],
       [("user", "第一条"), ("assistant", FULL), ("user", "第二条"), ("assistant", FULL)])
    win.input.clear()
    win.close()


def case_on_done_overwrites_bubble() -> None:
    """`_on_done` 必须用它自己收到的那份文本来定气泡，不能指望 `_on_chunk` 攒的那份。

    这条是白盒的，理由：正常路径上 `pending_text`（chunk 累加）和 `full`（worker 的
    `"".join(parts)`）**结构性恒等** —— 每个 `parts.append` 都配了一次 `chunk.emit`，
    队列连接又保证按序投递，所以端到端跑出来的断言**分不出**这两条路径是否都还在。
    可 `full` 才是落盘用的那份，一旦哪天两者不同步（Task 6 换真气泡时最容易出这种事），
    用户看到的就是「气泡里一句话、盘里另一句」。这里直接把两者摆成不一样，验 `_on_done`
    认的是自己手里那个入参。
    """
    print("_on_done 的文本权威性：")
    chat_store.clear()
    win = new_window({"api_key": "sk-test"})
    win.input.setPlainText("在吗")
    win.send()
    pend = win.pending

    # 这条用例是**同步直调** _on_done 的，而 _on_done 会把 self.worker 置 None；紧跟的
    # close() → _stop_worker() 就撞上 `if self.worker is None: return` 早退，那条真线程
    # 既没 stop 也没 disconnect。复审实测过：用例结束后泵 800ms，_on_done **第二次**跑到
    # 槽里，内存 history 2→3、盘里也 2→3，凭空多一条重复 assistant。所以先把引用接住，
    # 断言做完自己收尾。
    leaked = win.worker
    # M14：ChatWorker 是不给 parent 的。上面接住引用之后这一条就不再靠崩溃暴露了
    # （线程被漏掉时窗口析构会 abort），改成明写的一句断言守着它 —— 生命周期得跟着窗口走。
    ck("worker 挂在窗口上（生命周期跟着窗口走）", win.worker.parent(), win)

    win.pending_text = "攒歪了的字"
    pend.set_text("攒歪了的字")
    win._on_done(FULL)

    ck("气泡被覆盖成 _on_done 收到的文本", pend.text(), FULL)
    ck("落盘的也是那份", chat_store.load()[-1]["content"], FULL)
    # 上面两条已经蕴含了「气泡和落盘一致」，单独再写一条是永远不可能红的空断言，
    # 不加。
    leaked.stop()                  # 被 _on_done 撇下的那条活线程，自己收干净
    leaked.disconnect()
    leaked.wait(3000)
    win.close()


class _WedgedWorker:
    """一个 `terminate()` 杀不掉的 worker —— 只为了把 I3 那条兜底分支跑到。

    不是 QThread，所以想桩什么就桩什么。真的 `QThread` 上做不出这个桩：`wait()` /
    `isRunning()` 是 sip 生成的槽，实例上覆盖不了，`self.worker.wait = lambda ms: False`
    会直接报 AttributeError。整个换成普通 Python 对象就绕开了这件事 —— `_stop_worker`
    只用到 `stop` / `disconnect` / `wait` / `terminate` / `isRunning` 五个方法，桩得出来。
    """

    def __init__(self):
        self.waits = []
        self.terminate_called = False

    def stop(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def wait(self, ms: int) -> bool:
        self.waits.append(ms)
        return False                     # 第一次等超时、强杀之后再等还是超时

    def terminate(self) -> None:
        self.terminate_called = True

    def isRunning(self) -> bool:
        return True                      # 强杀之后照样活着


def case_wedged_worker_keeps_ref() -> None:
    """I3 的兜底分支：`terminate()` 之后线程还活着时，**绝不能把引用丢掉**。

    白盒，理由：这一支在真机上**走不到** —— Windows 的 `terminate()` 走 `TerminateThread`，
    `wait(500)` 回来 `isRunning()` 稳定是 False（跑 C1 那个探针时实测过），所以拿真线程
    构造不出「杀不死」这个状态。可它守的恰恰是 C1 那条 abort 路：线程还活着而引用丢了，
    窗口析构时 Qt 照样 abort。既然构造不出真状态，就把 worker 换成一个 terminate()
    杀不掉的桩，直接验分支本身。
    """
    print("兜底：terminate 没杀干净时保住引用：")
    chat_store.clear()
    win = new_window({"api_key": "sk-test"})
    stub = _WedgedWorker()
    win.worker = stub
    win.pending_text = "半句"
    win.pending = win._append_widget("半句", False)   # 顺手验这一支也会清半句
    win._set_busy(True)                               # 走到这一支时按钮本来就停在「停止」

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):             # 不动产品代码，只在外面接 print
        win._stop_worker()
    log = buf.getvalue()

    ck("两次 wait 都被调到了", stub.waits, [3000, 500])
    ck("强杀确实试过了", stub.terminate_called, True)
    # 最要紧的一条。丢了它，线程还活着而窗口析构时 Qt 直接 abort(0xC0000409) —— C1。
    ck("线程还活着时没把 worker 置 None", win.worker, stub)
    ck("打了「强杀没成功」那行日志", "强杀没成功" in log, True)
    ck("这一支也把半句气泡清了", win.pending, None)
    ck("攒的残字也清了", win.pending_text, "")
    # 这一支**有意**不把按钮切回「发送」（chat_window.py 那儿有注释说明）：
    # 线程是真的卡住了，显示「发送」的话用户点下去会被 send() 里的守卫静默吞掉。
    ck("按钮停在「停止」，不假装能再发", win.btn.text(), "停止")

    # 桩没法真的收尾，摘掉再关窗，别让它挂到窗口析构那一刻
    win.worker = None
    win.close()


def case_bubbles_painted() -> None:
    """视觉里能自动验的四条：气泡真画出来了、两边底色对、75% 上限、头像框贴合立绘。

    Step 4 那八条里剩下的四条要肉眼（版面好不好看），留在用户手上 —— `--shot`
    只负责把图生成好。这里不验「好不好看」，也不假装验过。
    """
    print("气泡视觉：")
    chat_store.clear()
    win = new_window({"api_key": "sk-test"})
    win.resize(420, 560)
    win.show()          # 布局要真跑过一遍，grab() 才拿得到几何
    pump(50)

    mine = win._append_widget("我这边说的", True)
    hers = win._append_widget("她那边说的喵", False)
    pump(50)

    # 1+2：气泡顶部正中那一点。正常渲染下它落在填充区里 —— 圆角够不着、1.2px 的
    # 边框线也够不着。paintEvent 被掏空的话那一点会落回 QWidget.grab() 的兜底底色
    # #EFEFEF（实测），跟两边任何一个填充色都不等，所以这两条会一起红。
    ck("她的气泡填的是 HERS_BG，不是空白",
       pixel(hers, hers.width() // 2, 2), CW.HERS_BG.rgb())
    ck("我的气泡填的是 MINE_BG，不是空白",
       pixel(mine, mine.width() // 2, 2), CW.MINE_BG.rgb())

    # 3：75% 上限。视口宽度不写死 —— 滚动条在不在会让它差 8px，按当前值算。
    vp = win.scroll.viewport().width()
    ck("两个气泡的宽度上限都是视口的 75%",
       (mine.maximumWidth(), hers.maximumWidth()), (int(vp * 0.75), int(vp * 0.75)))

    # 3 的另一半：窗口缩放之后要跟着变
    win.resize(700, 560)
    pump(50)
    vp2 = win.scroll.viewport().width()
    ck("缩放确实改变了视口宽度（否则下一条是白给的）", vp2 != vp, True)
    ck("缩放窗口后两个气泡的上限跟着变",
       (mine.maximumWidth(), hers.maximumWidth()), (int(vp2 * 0.75), int(vp2 * 0.75)))

    # 3 的第三半：窗口最后一次缩放**之后**新加的气泡也得吃到。只在 resizeEvent 里刷的
    # 实现漏掉的正是这一条 —— 新气泡一直拿着默认上限，长消息会直接顶满整行。
    late = win._append_widget("缩放之后才加的一条消息", False)
    ck("缩放之后新加的气泡也吃到 75% 上限", late.maximumWidth(), int(vp2 * 0.75))

    # 4：头像框贴合缩放后的立绘。原图 369×800 缩到 56 高只有 26 宽；框成 56×56 的话
    # 右边会空出 30px，正好夹在头像和气泡中间把它们推远 —— 用户一眼能看见的就是这个。
    pm = CW._avatar_pixmap()
    ck("立绘按原比例缩到 26 宽 56 高", (pm.width(), pm.height()), (26, 56))
    avatar = hers._outer.layout().itemAt(0).widget()
    ck("头像控件的宽度就是立绘的宽度（右边不留空）", avatar.width(), pm.width())
    ck("头像控件的高度就是立绘的高度", avatar.height(), pm.height())

    # 5：配色只有一个来源（计划的 Global Constraints：「沿用 toast.py 已有的
    # BG / BORDER / TEXT / DIM，不要另起一套」）。这儿查的是 QSS 里那串值确实是从
    # toast 拼出来的 —— 手抄一份的话这几条会红，而手抄的那份已经抄错过两处
    # （窗口底色 vs BG、系统提示 vs DIM），改桌宠配色聊天窗也不会跟着变。
    ck("窗口底色用的是 toast.BG", CW.BG.name() in CW.QSS, True)
    ck("描边用的是 toast.BORDER",
       f"rgba({CW.BORDER.red()}, {CW.BORDER.green()}, {CW.BORDER.blue()}, 150)" in CW.QSS,
       True)
    ck("输入框文字用的是 toast.TEXT", CW.TEXT.name() in CW.QSS, True)
    # 气泡文字是**另一条** inline stylesheet（在 _Bubble.__init__ 里，不在模块级
    # QSS 里），上面那条查 QSS 的看不见它 —— `#3A2E34` 就是手抄在那儿的。
    # 查值没用：手抄一份值一模一样的，比字符串照样绿。只有把来源换掉才知道它跟不跟。
    real_text = CW.TEXT
    CW.TEXT = CW.QColor(1, 2, 3)           # 换一个正常不可能撞上的颜色
    try:
        follows = win._append_widget("换个颜色再看", False)._label.styleSheet()
        white = win._append_widget("白字那条不跟", True)._label.styleSheet()
    finally:
        CW.TEXT = real_text
    ck("换掉 toast.TEXT，气泡文字跟着换（换不动的就是手抄的）",
       "#010203" in follows, True)
    # 反面：我的气泡是粉底白字，跟 TEXT 无关 —— 没有这条的话「两边都吐 TEXT」
    # 也能过上面那条，等于没测。
    ck("我的气泡是白字，不跟着 TEXT 走", "#010203" in white, False)
    ck("气泡描边常量就是 toast.BORDER", CW.HERS_LINE.rgb(), CW.BORDER.rgb())
    win._append_notice("测试用的提示")
    # _add() 是插在末尾那个弹簧**前面**的，所以提示在 count()-2，末尾还是弹簧
    notice = win.msgs.itemAt(win.msgs.count() - 2).widget()
    ck("系统提示文字用的是 toast.DIM", CW.DIM.name() in notice.styleSheet(), True)
    win.close()


def case_stream_follows_scroll() -> None:
    """Critical 1：聊天窗要跟着流式回复往下滚，但用户往上翻的时候不能拽他。

    这一条以前完全没有覆盖（三套测试里 value / maximum 一次都没出现过），所以
    「她一回话就滚不下去」这个主路径缺陷一路绿灯走到了终审。实测的旧行为：
    用户明明在底部，第一块 chunk 滚到的却是**旧的** maximum（Qt 的重排是延迟的），
    滚完 value 没动而 max 涨上去了，于是 _at_bottom() 从此恒为 False，后面 11 块
    再也没滚过 —— 不是「滚不到位」，是「滚一次就永久放弃」。
    """
    print("流式回复跟随滚动：")
    chat_store.clear()
    # 历史必须超过一屏，否则滚不滚都看不出区别（这一条是整条用例的前提）
    chat_store.save([chat_store.make("user" if i % 2 == 0 else "assistant",
                                     f"第 {i} 条，写长一点点好占满一整行喵")
                     for i in range(40)])
    win = new_window({"api_key": "sk-test"})
    win.resize(420, 560)
    win.show()          # 要真布局过一遍，滚动范围才是真的
    pump(300)

    bar = win.scroll.verticalScrollBar()
    ck("历史够长、真的滚得动（否则下面几条是白给的）", bar.maximum() > 0, True)
    ck("开窗之后停在底部", win._at_bottom(), True)

    # 直接喂增量走 _on_chunk —— 这正是她流式回话时走的那条路，不用起网络
    win.pending_text = ""
    win.pending = win._append_widget("", False)
    pump(50)
    ck("流式开始时在底部（此刻不在的话这条测的就不是「跟随」）",
       win._at_bottom(), True)
    max0 = bar.maximum()

    for _ in range(12):
        win._on_chunk("喵喵喵喵喵喵喵喵喵喵喵喵喵喵喵喵喵喵喵喵\n")
        pump(30)

    ck("流式期间内容真的长高了（否则「贴着底部」是白给的）",
       bar.maximum() > max0, True)
    ck("流式过程中始终贴着底部", bar.value() >= bar.maximum() - 4, True)
    ck("就是底部，不是「差几个像素」", bar.maximum() - bar.value(), 0)

    # 反过来的那条：用户自己往上翻之后不许再被拽下去
    bar.setValue(0)
    pump(50)
    ck("往上翻之后不再跟随", win._stick, False)
    before = bar.value()
    for _ in range(6):
        win._on_chunk("再多一行喵\n")
        pump(30)
    ck("往上翻着的时候喂 chunk 不会被拽下去", bar.value(), before)
    win.close()


class _FakeDwm:
    """一个固定返回某个 HRESULT 的 dwmapi 替身。只能这么测 —— 真 DWM 在 Win11 上
    永远回 S_OK，那正是不接 HRESULT 也看不出来的原因。"""

    def __init__(self, hr: int):
        self.hr = hr
        self.attrs = []

    def DwmSetWindowAttribute(self, hwnd, attr, ptr, size):   # noqa: N802  照着真名写
        self.attrs.append(attr.value)
        return self.hr


def case_titlebar_rejects_hresult() -> None:
    """W11：paint_titlebar 必须接住非零 HRESULT，不能当成功。

    返回值是这件事上**唯一**存在的信号（DwmGetWindowAttribute 读不回来，见那儿
    的注释），所以「DWM 明确拒绝」和「上色成功」只能靠 HRESULT 区分。不接的话外面
    那层 try 抓不到非零返回码，结果是 _titlebar_painted 被置上（不再重试）而一行
    日志都没有。把 `if hr != 0` 整段删掉，改前两套测试照样全绿。

    打桩 dwm，而不是靠 offscreen 那个 E_HANDLE：后者在别的平台上会假红。
    """
    print("标题栏：DWM 回非零 HRESULT 时不能当成功：")
    if sys.platform != "win32":
        print("    跳过：非 Windows 上 paint_titlebar 直接返回 False，那是设计内的")
        return
    import ctypes
    win = new_window({})
    real_windll = ctypes.windll
    buf = io.StringIO()
    try:
        ctypes.windll = type("_W", (), {"dwmapi": _FakeDwm(0x80070006)})()
        with contextlib.redirect_stdout(buf):
            ok = CW.paint_titlebar(win, "#FBE3EA", "#3A2E34")
        bad = ctypes.windll.dwmapi
        # 正面控制：同一个替身回 S_OK 时必须成功。没有这条的话「恒返回 False」
        # 也能过上面那条断言，等于没测。
        ctypes.windll = type("_W", (), {"dwmapi": _FakeDwm(0)})()
        with contextlib.redirect_stdout(io.StringIO()):
            ok_good = CW.paint_titlebar(win, "#FBE3EA", "#3A2E34")
        good = ctypes.windll.dwmapi
    finally:
        ctypes.windll = real_windll
    ck("DWM 拒绝时返回 False", ok, False)
    # 第一个属性被拒绝就收手（不接着试第二个、更不假装成功）—— 这儿的实现就是
    # 这么写的，钉住它免得以后有人把它改成「继续试下一个」
    ck("第一个属性被拒绝就收手", bad.attrs, [35])
    ck("打了带 HRESULT 的日志", "HRESULT=0x80070006" in buf.getvalue(), True)
    ck("DWM 接受时返回 True（否则上一条是恒真的）", ok_good, True)
    ck("接受那一轮两个属性也都试了", good.attrs, [35, 36])
    win.close()


def case_restore_geometry() -> None:
    """W12：存下来的窗口几何要真的用上。

    用户会直接骂的一条 —— 窗口位置和大小每次开窗都忘。改前没有任何断言覆盖
    _restore_geometry 里那个 setGeometry（把它换成「忽略存的值」套件全绿）。
    """
    print("窗口几何回填：")
    cfg = dict(cfgmod.DEFAULTS)
    cfg["chat_geometry"] = [123, 77, 456, 600]
    win = new_window(cfg)
    ck("建出来的窗口按存下来的几何摆好了",
       [win.x(), win.y(), win.width(), win.height()], [123, 77, 456, 600])
    win.close()

    # 反面：没存过就落到默认大小 —— 不然「永远用一个写死的几何」也能过上面那条
    win2 = new_window(dict(cfgmod.DEFAULTS))
    ck("没存过就用默认大小", (win2.width(), win2.height()), (420, 560))
    win2.close()


def case_titlebar() -> None:
    """标题栏上色（Step 3）：这条能真测，但必须在**原生平台**的子进程里测。

    主进程为了无头跑测试把 QT_QPA_PLATFORM 钉在 offscreen 上，而 offscreen 压根没有
    原生窗口 —— 实测 `int(win.winId())` 是 1，DwmSetWindowAttribute 回 E_HANDLE
    （0x80070006），于是 paint_titlebar 返回 False。那**不是**产品代码的问题，是平台的
    问题，在那一层断言它就是假红。

    子进程用默认的 windows 平台 + `WA_DontShowOnScreen`：建得出真 HWND（实测是个七位数
    的真句柄）、又不映射到屏幕上，不会在用户桌面上闪一下。实测两个 DwmSetWindowAttribute
    都回 S_OK，所以那边可以断言 True。

    非 Win11 直接跳过：DWMWA_CAPTION_COLOR 是 Win11 才有的，Win10 退回系统默认配色
    本来就是设计内的行为（这个仓库没有 CI），别让它变成假红。
    """
    print("标题栏上色（子进程，原生平台）：")
    if sys.platform != "win32":
        print("    跳过：不是 Windows，paint_titlebar 返回 False 是设计内的")
        return
    build = sys.getwindowsversion().build
    if build < 22000:
        print(f"    跳过：Windows build {build} 没有 DWMWA_CAPTION_COLOR（Win11 才有），"
              f"退回系统默认配色是设计内的")
        return
    env = dict(os.environ, PYTHONUTF8="1")
    me = str(Path(__file__).resolve())   # 平台由子进程自己改成 windows，见 _titlebar_probe
    r = subprocess.run([sys.executable, "-u", me, "--titlebar-probe"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=120)
    out = r.stdout or ""
    ck("子进程里拿到的是真 HWND（不是 offscreen 那个 1）", "真HWND=True" in out, True)
    # 返回值是这件事上唯一存在的信号 —— DwmGetWindowAttribute 读不回来（E_INVALIDARG）。
    ck("两个 DwmSetWindowAttribute 都回 S_OK，标题栏真的上色了",
       "painted=True" in out, True)
    if r.returncode or "painted=True" not in out:
        print(f"    子进程 stdout：{out.strip()}")
        print(f"    子进程 stderr：{(r.stderr or '').strip()}")
    ck("子进程正常退出", r.returncode, 0)


def case_menu_entry() -> None:
    """pet.py 的入口：三项菜单都在，连点两次只开一个窗口（brief Step 3 的第 4 条）。

    没有 ChatWindow 之外的入口能测到 pet.py 那几行接线，所以顺手在这儿守一下 ——
    不然「和她说话」写错了要等真跑起来才发现。
    """
    print("菜单入口：")
    pet = new_pet()
    if pet is None:
        return
    try:
        menu = pet.build_menu()
        items = [a.text() for a in menu.actions()]
        ck("菜单里有「和她说话」「编辑人设」「清空对话记录」",
           [t for t in ("和她说话", "编辑人设", "清空对话记录") if t in items],
           ["和她说话", "编辑人设", "清空对话记录"])
        # W16：只查菜单里有没有这几个字是不够的 —— addAction 的第二个参数接错了
        # 方法，字面照样在。实测「和她说话」接到 refresh_balance 上时整条套件全绿，
        # 而用户点下去是「刷新余额」。trigger() 走的就是真点击那条路。
        ck("触发之前没开着聊天窗", getattr(pet, "chat", None), None)
        talk = [a for a in menu.actions() if a.text() == "和她说话"][0]
        talk.trigger()
        ck("「和她说话」真的开出了聊天窗口",
           type(getattr(pet, "chat", None)).__name__, "ChatWindow")
        first = pet.chat
        pet.open_chat()                                 # 再点一次
        ck("连点两次只有一个窗口", pet.chat is first, True)

        # 「清空对话记录」光查菜单里有没有这几个字还不够 —— addAction 的第二个参数
        # 接错了方法照样是绿的，得真走一遍 pet → chat。确认框打桩成「是」（真弹窗会
        # 挂住；这里清的是隔离出来的临时目录，动不到用户的聊天记录）。
        # 「编辑人设」不在这儿调：它最后走 QDesktopServices 弹系统程序，会把记事本
        # 真的开到用户桌面上，不是测试该干的事，那条留给手动验证。
        first.history = [chat_store.make("user", "随便一句")]
        chat_store.save(first.history)
        real_question = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            pet.clear_chat()
        finally:
            QMessageBox.question = real_question
        ck("「清空对话记录」真的走到聊天窗口里去了", first.history, [])
        ck("盘里也清了", chat_store.load(), [])

        first.close()                                   # closeEvent 也只写临时目录
    finally:
        pet.close()


def case_double_click() -> None:
    """双击她 = 开聊天窗，右键双击不开（brief Step 2 的双击入口）。

    直接造 QMouseEvent 调进去，不 show() —— 造事件不需要窗口显示，也就不会往桌面上
    放第二只塔菲（用户那只正在跑）。五参构造（QPointF 那版）在本机实测可用。
    """
    print("双击开窗：")
    pet = new_pet()
    if pet is None:
        return
    try:
        def dbl(button):
            return QMouseEvent(QEvent.MouseButtonDblClick, QPointF(5.0, 5.0),
                               button, button, Qt.NoModifier)
        ck("一开始没开窗", getattr(pet, "chat", None), None)
        # 右键那条必须先验。倒过来写（先左键、后右键）的话，第二步会撞上「已经开着
        # 就不再开」的守卫，无论 mouseDoubleClickEvent 判不判 button 都会绿 —— 一条
        # 永远正确的假断言。
        pet.mouseDoubleClickEvent(dbl(Qt.RightButton))
        ck("右键双击不开窗", getattr(pet, "chat", None), None)
        pet.mouseDoubleClickEvent(dbl(Qt.LeftButton))
        ck("左键双击开了聊天窗", type(getattr(pet, "chat", None)).__name__, "ChatWindow")
    finally:
        pet.close()


def case_quit_clears_chat() -> None:
    """右键 →「退出」之后不能再攥着聊天窗（线程要跟着收干净）。

    直接在本进程调 quit()：里面那句 QApplication.quit() 在没有 exec_ 的事件循环上
    是空操作（Qt 文档原话：事件循环没在跑就什么都不做），不会把这个进程带走。子进程
    那条路（真跑 exec_ 的）已经是 case_quit_while_streaming 在守了。
    """
    print("退出时收掉聊天窗：")
    pet = new_pet()
    if pet is None:
        return
    try:
        pet.open_chat()
        ck("先确认窗真的开着", type(getattr(pet, "chat", None)).__name__, "ChatWindow")
        pet.quit()
        ck("退出后不再攥着聊天窗", getattr(pet, "chat", None), None)
    finally:
        pet.close()


def case_close_chat_wedged() -> None:
    """白盒：强杀不掉时 _close_chat 绝不能把 chat 置 None。

    这一支在真机上**走不到** —— Windows 的 terminate() 走 TerminateThread，
    wait(500) 回来 isRunning() 稳定是 False（Task 5 跑 C1 探针时实测过）。所以跟
    case_wedged_worker_keeps_ref 一个套路：把 worker 整体换成一个杀不掉的鸭子类型
    对象（真的 QThread 上覆盖不了 wait/isRunning，那些是 sip 生成的槽），直接验
    _close_chat 里那个 `if chat.worker is None` 守卫本身。

    没有守卫会怎样：chat.close() 是同步走完 closeEvent → _stop_worker 的，而那一支
    **有意**留着 self.worker 不置 None（线程还卡在网络上）；紧接着 `self.chat = None`
    就丢掉了 ChatWindow 的最后一个 Python 引用 → 窗口析构 → ChatWorker 是它的 Qt
    子对象跟着析构 → QThread 析构时线程还在跑 → qFatal → abort 0xC0000409。这正是
    Task 5 复审抓出的 C1，而 aboutToQuit 救不了：崩在 _close_chat() 里面，根本走不到
    QApplication.quit()。
    """
    print("兜底：强杀不掉时退出不能丢聊天窗引用：")
    pet = new_pet()
    if pet is None:
        return
    try:
        pet.open_chat()
        win = pet.chat
        stub = _WedgedWorker()
        win.worker = stub

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):           # 不动产品代码，只在外面接 print
            pet._close_chat()

        ck("强杀确实试过了", stub.terminate_called, True)
        # 最要紧的一条。丢了它，窗口析构时 Qt 直接 abort(0xC0000409)，pythonw 下用户
        # 只看到程序凭空消失，什么线索都没有。
        ck("线程还活着时没把 chat 丢掉", pet.chat is win, True)

        # 反面：线程真收干净了（worker is None）就必须放手 —— 不然「干脆一直攥着」
        # 也能过上面那条，等于没测。
        win.worker = None
        pet._close_chat()
        ck("线程收干净了就放手", getattr(pet, "chat", None), None)
    finally:
        # 桩没法真的收尾，摘掉再关窗，别让它挂到窗口析构那一刻
        if getattr(pet, "chat", None) is not None:
            pet.chat.worker = None
        pet.close()


def case_config_geometry_roundtrip() -> None:
    """chat_geometry 存得下来，也要读得回来。

    这条守的是 config.load() 里那句 `if k in DEFAULTS` 过滤：DEFAULTS 里漏了这一项
    的话，ChatWindow 每次关窗都老老实实把它写进文件，读的时候却被悄悄丢掉 —— 表现
    是「窗口位置和大小永远记不住」，而盘里明明躺着那个数，查起来很费劲。
    """
    print("配置里的窗口几何：")
    cfg = dict(cfgmod.DEFAULTS)
    cfg["chat_geometry"] = [120, 80, 420, 560]
    cfgmod.save(cfg)
    back = cfgmod.load()
    ck("存下来的几何读得回来", back.get("chat_geometry"), [120, 80, 420, 560])
    ck("在 DEFAULTS 里（load 的过滤才留得住它）", "chat_geometry" in cfgmod.DEFAULTS, True)


def _atomic_save_case(label, path, save, load, first, second, read,
                      log_word, unlinks_tmp_on_fail) -> None:
    """一条原子写用例，对 config.save / chat_store.save 各跑一遍。

    为什么要各跑一遍：这条用例原来打桩的是 **config.save**，而真正每轮回复都要覆写
    一次的是 chat_store.save —— 那份里装着用户全部的对话历史，写到一半被杀/断电就
    把记录弄废，正是原子写要防的事。实测：chat_store.save 改成非原子直写，
    test_units 和 test_chat_window **两个都全绿**（对照组的 config 同一改法红 4 项，
    说明手法没问题，缺口精确地落在 chat_store 上）。
    """
    print(f"{label}原子写：")
    tmp = path.parent / (path.name + ".tmp")
    save(first)
    ck(f"{label}：存完之后没留下 .tmp", tmp.exists(), False)
    ck(f"{label}：存进去的读得回来", read(load()), read(first))

    # 替换失败：磁盘满、杀软锁住目标、文件被别的程序占用都会这样。打桩的是 os.replace
    # 本身，所以非原子写（直接 write_text 覆盖目标）在这条下会当场露馅 —— 目标文件
    # 已经被新内容覆盖掉一半了。断言的就是「宁可这次没存上，也不能把上一次存好的弄坏」。
    before = path.read_text(encoding="utf-8")
    real_replace = os.replace

    def boom(*a, **k):
        raise OSError("打桩：替换这一步失败")

    os.replace = boom                 # 两个模块的 save() 里用的都是 os.replace
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):           # 这一笔失败是故意的，别刷到屏幕上
            save(second)
    finally:
        os.replace = real_replace
    # 比整份文本而不是只看某一个字段：后者过得去、文件却可能已经被写坏成半截
    ck(f"{label}：原文件一字没变", path.read_text(encoding="utf-8"), before)
    ck(f"{label}：读回来还是老值", read(load()), read(first))
    # 失败必须留下痕迹：静默吞掉的写失败，用户只会看到「记录又没了」而查不出原因
    ck(f"{label}：打了一行日志", log_word in buf.getvalue(), True)
    # 失败之后那份 .tmp 里装着完整内容（聊天记录那份是整份对话），不能让它躺在盘上。
    # config.save 那一支**有意**不删（docstring 里把「死在中间会留下 .tmp」写成了已知
    # 代价），所以这条只对 chat_store 断言。
    if unlinks_tmp_on_fail:
        ck(f"{label}：失败之后也没留下 .tmp", tmp.exists(), False)


def case_atomic_save() -> None:
    """config.save() 和 chat_store.save() 都必须是原子写。

    不是「好看」而已：config 里有 API Key、chat.json 里有全部对话，非原子写正好在
    写到一半时被杀/断电，文件就废了 —— 表现是「每次启动 Key 都变空」「聊天记录全
    没了」，用户一点线索都没有。
    """
    cfg = dict(cfgmod.DEFAULTS)
    # 值必须跟 DEFAULTS 里的**不一样**（height 的默认是 200）。用默认值的话 load()
    # 无论文件在不在、是不是刚写的都会返回它，下面那条断言就成了恒真的摆设 ——
    # 名字声称验了一次往返，实际什么都没验。
    cfg["height"] = 240
    _atomic_save_case(
        "配置", cfgmod.CONFIG_PATH, cfgmod.save, cfgmod.load,
        cfg, {**cfg, "height": 999},
        lambda c: c["height"], "存不了 config.json", False)
    _atomic_save_case(
        "聊天记录", chat_store.CHAT_PATH, chat_store.save, chat_store.load,
        [chat_store.make("user", "在吗")],
        [chat_store.make("user", "在吗"), chat_store.make("assistant", "在呢喵")],
        lambda ms: [m["content"] for m in ms], "存不了聊天记录", True)


def case_avatar_cached() -> None:
    """头像立绘只解码一次（Task 6 留下的惰性缓存，之前没有任何断言守着它）。

    回填 200 条历史时 _append_widget 要走 100 次，去掉缓存就是每次从磁盘解码一张
    369×800 的图再缩放 —— 开窗时肉眼可见的卡顿。这里必须用身份比较：把缓存去掉
    之后每次 new 一个 QPixmap，尺寸当然还是一样的，「宽高相同」那种断言照样绿。
    """
    print("头像立绘缓存：")
    ck("两次拿到的是同一个对象", CW._avatar_pixmap() is CW._avatar_pixmap(), True)


def case_quit_while_streaming() -> None:
    """流式中退出程序：进程不能硬崩（C1 的回归护栏）。

    这条只能在**子进程**里测。崩的是 Qt 的 abort（0xC0000409），不是 Python 异常：
    main.py 的 sys.excepthook 接不住，那个进程里什么断言、什么打印都可能留不下来，
    唯一拿得到的证据是父进程读到的 returncode。所以子进程只负责复刻
    「流式中右键 → 退出」这条路，判断由这里做。
    """
    print("流式中退出程序（子进程）：")
    env = dict(os.environ, PYTHONUTF8="1")
    me = str(Path(__file__).resolve())
    r = subprocess.run([sys.executable, "-u", me, "--quit-probe"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=180)
    # 先看它有没有真的进到流式 —— 没有的话 returncode 0 是白给的
    ck("子进程确实停在流式中", "流式中" in (r.stdout or ""), True)
    ck("退出程序时进程正常结束（没 abort）", r.returncode, 0)
    if r.returncode:
        print(f"    子进程 stdout：{(r.stdout or '').strip()}")
        print(f"    子进程 stderr：{(r.stderr or '').strip()}")


# ---------- 子进程模式 ----------
def _titlebar_probe() -> int:
    """子进程模式：用原生平台建一个不映射到屏幕的真窗口，验标题栏上色。

    **这里不打断言**，理由跟 _quit_probe 一样：判断留给父进程那个 case_titlebar，
    这一层只负责把事实（HWND 是不是真的、paint_titlebar 返回了什么）打出来。
    """
    tmp = tempfile.mkdtemp(prefix="taffy-titlebar-")
    _isolate_paths(tmp)
    # 文件头为了无头测试把平台钉成 offscreen 了，这一条就是来测原生窗口的，得改回来。
    # 平台插件是在 QApplication 构造时选的，所以在这里改还来得及。
    os.environ["QT_QPA_PLATFORM"] = "windows"
    app = QApplication([sys.argv[0]])           # noqa: F841  别把 --titlebar-probe 塞给 Qt
    win = CW.ChatWindow({})
    win.setAttribute(Qt.WA_DontShowOnScreen, True)   # 建真窗口，但不映射到屏幕上
    win.show()
    pump(200)
    hwnd = int(win.winId())
    print(f"[titlebar-probe] winId={hwnd} 真HWND={hwnd > 0xFFFF} "
          f"painted={bool(getattr(win, '_titlebar_painted', False))}", flush=True)
    win.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


def _quit_probe() -> int:
    """子进程模式：忠实复刻 main.py 的启动/退出 —— 流式中直接 QApplication.quit()。

    **这里不要跑断言。** 一旦崩了，这个进程里的输出可能整个丢掉，什么都判不了。
    """
    from PyQt5.QtCore import Qt
    from taffy_pet.pet import PetWindow

    tmp = tempfile.mkdtemp(prefix="taffy-quitprobe-")
    _isolate_paths(tmp)
    app = QApplication([sys.argv[0]])          # 别把 --quit-probe 塞给 Qt
    app.setQuitOnLastWindowClosed(False)
    srv, url = TC.serve()
    CH.API_URL = url
    TC.Handler.delay = 0.05
    TC.Handler.gate = threading.Event()        # 服务端发完第一行就卡住，线程必然停在读上
    try:
        cfg = cfgmod.load()
        cfg["api_key"] = "sk-test"
        pet = PetWindow(cfg)
        pet.show()
        pet.open_chat()
        win = pet.chat
        win.input.setPlainText("在吗")
        win.send()

        def poll():
            if win.pending_text:
                print(f"[quit-probe] 流式中 pending_text={win.pending_text!r}，现在退出程序",
                      flush=True)
                pet.quit()                     # 右键 →「退出」；聊天窗从没 close() 过
                return
            QTimer.singleShot(20, poll)

        QTimer.singleShot(0, poll)
        rc = app.exec_()
        print(f"[quit-probe] exec_ 返回 {rc}", flush=True)
        return rc
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


def _shot() -> int:
    """目检模式：把一段假对话渲染成 assets/_chat.png，省掉「自己跑一遍程序」。

    **这里也走原生平台**，不用 offscreen。实测 offscreen 下一个字都画不出来 ——
    假接口那套字体后端拿不到字形，气泡、按钮、输入框全是空的，图就成了一张没有
    文字的线框，目检个啥。所以跟 _titlebar_probe 一样换成 windows 平台，再用
    `WA_DontShowOnScreen` 保证窗口不映射到屏幕上。

    **图里不会有标题栏**：`grab()` 只抓客户区，系统标题栏是非客户区，本来就不在里面
    —— 上没上色从这张图上看不出来（虽然这一跑确实把它刷粉了），标题栏的验证走
    case_titlebar。
    """
    tmp = tempfile.mkdtemp(prefix="taffy-chatshot-")
    _isolate_paths(tmp)
    os.environ["QT_QPA_PLATFORM"] = "windows"   # offscreen 画不出字，见上面那段
    app = QApplication([sys.argv[0]])           # noqa: F841  建控件要用，必须接住引用
    chat_store.save([
        chat_store.make("user", "在吗"),
        chat_store.make("assistant", "在呢喵～今天想聊点什么？"),
        chat_store.make("user", "今天有点累，随便聊聊吧"),
        chat_store.make("assistant", "那就先把肩膀放松一下，别看屏幕啦。\n我在这儿陪你。"),
    ])
    win = CW.ChatWindow({})
    win.setAttribute(Qt.WA_DontShowOnScreen, True)   # 建真窗口，但不映射到屏幕上
    win.resize(420, 560)
    win.show()
    pump(300)
    out = Path(CW.ASSETS) / "_chat.png"
    ok = win.grab().save(str(out))
    print(f"目检图：{out}（{'保存成功' if ok else '保存失败'}）")
    print("    注意：grab() 只抓客户区，系统标题栏不在图里 —— 不是上色坏了。")
    win.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


def case_day_separator() -> None:
    """日期分隔线：跨天才插一条，同一天多少条都只插一条。

    `texts()`/`rows()` 按 objectName 把分隔线排掉了（见那两个函数），所以这里
    单独查它 —— 排掉不等于不测。
    """
    print("日期分隔线：")
    from datetime import datetime, timedelta

    # 纯函数先测：解析不出来必须给空串，不能抛 —— 为一条分隔线把窗口打不开不值得。
    ck("今天", CW.day_label(datetime.now().isoformat(timespec="seconds")), "今天")
    ck("昨天", CW.day_label(
        (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")), "昨天")
    ck("没有 ts 给空串", CW.day_label(""), "")
    ck("ts 是垃圾也给空串", CW.day_label("不是时间"), "")

    chat_store.clear()
    today = datetime.now()
    old = today - timedelta(days=3)
    chat_store.save([
        {"role": "user", "content": "三天前说的",
         "ts": old.isoformat(timespec="seconds")},
        {"role": "assistant", "content": "三天前回的",
         "ts": old.isoformat(timespec="seconds")},
        chat_store.make("user", "今天说的"),
    ])
    win = new_window({})
    days = [w.text() for i in range(win.msgs.count())
            if (w := win.msgs.itemAt(i).widget()) is not None
            and w.objectName() == CW.DAY_OBJECT]
    ck("两条三天前 + 一条今天，插两条分隔", len(days), 2)
    ck("分隔按时间先后", days[0] != days[1], True)
    ck("最后一条是今天", days[-1], "今天")
    win.close()


def main() -> int:
    # 环境变量优先于 config.json，真设了的话「没 Key」那条用例就不成立了
    os.environ.pop("DEEPSEEK_API_KEY", None)

    app = QApplication(sys.argv)                        # noqa: F841  建控件要用
    tmp = tempfile.mkdtemp(prefix="taffy-chatwin-")
    _isolate_paths(tmp)
    srv, url = TC.serve()
    CH.API_URL = url                                    # 所有请求都打到假接口上
    try:
        run_case(case_no_key)
        run_case(case_send_then_stream)
        run_case(case_agent_tool_round)
        run_case(case_agent_off)
        run_case(case_stop_mid_stream)
        run_case(case_close_mid_stream)
        run_case(case_reopen_backfills)
        run_case(case_clear_history)
        run_case(case_two_sends_same_window)
        run_case(case_on_done_overwrites_bubble)
        run_case(case_bubbles_painted)
        run_case(case_stream_follows_scroll)
        run_case(case_wedged_worker_keeps_ref)
        run_case(case_titlebar)
        run_case(case_titlebar_rejects_hresult)
        run_case(case_quit_while_streaming)
        run_case(case_menu_entry)
        run_case(case_double_click)
        run_case(case_quit_clears_chat)
        run_case(case_close_chat_wedged)
        run_case(case_config_geometry_roundtrip)
        run_case(case_restore_geometry)
        run_case(case_atomic_save)
        run_case(case_avatar_cached)
        run_case(case_day_separator)
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    if "--titlebar-probe" in sys.argv:
        sys.exit(_titlebar_probe())
    if "--quit-probe" in sys.argv:
        sys.exit(_quit_probe())
    if "--shot" in sys.argv:
        sys.exit(_shot())
    sys.exit(main())
