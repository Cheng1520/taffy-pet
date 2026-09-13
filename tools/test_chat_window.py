"""聊天窗口的冒烟测试：能发、能收、能流式、能停、能关。

    PYTHONUTF8=1 python tools/test_chat_window.py

Task 5 的手动验证（真填一个 Key、发一句「你好」、肉眼看打字机效果和四条边界）
既没有 Key 也没人坐在 GUI 前面，做不了。这个文件把那几条换成无头断言：起一个
只回 SSE 的本地假接口（直接复用 tools/test_chat.py 那套），用 offscreen 平台建
真的 ChatWindow，把「发出去的消息落盘了没、流式的字拼全了没、点停止留没留住
已经吐出来的半句、流式中关窗会不会崩、重开窗口历史回没回来、右键菜单连点两次
会不会开出两个窗口」全部钉死。

**路径隔离（这条最要紧）**：非打包运行时 paths.DATA_DIR 就是仓库根目录，也就是说
仓库根下的 config.json 是用户真实的配置文件（里面有他的 DeepSeek API Key）、
chat.json 是他真实的聊天记录。而 ChatWindow.closeEvent 会调 cfgmod.save()、
聊天记录每次发送都会 chat_store.save() —— 测试里跑一遍真窗口就会把用户的东西
覆盖掉。所以建窗口之前必须先把这两个模块的路径常量指到临时目录去。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# 必须在任何 PyQt5 的 import 之前 —— 没有显示器也要能建窗口
os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# 假接口那套直接用 test_chat 的：它把 main() 藏在 __main__ 后面，import 没有副作用，
# 顺便还能白拿它模块级设的 NO_PROXY（绕开本机系统代理对 127.0.0.1 的劫持）。
import test_chat as TC                                  # noqa: E402

from PyQt5.QtCore import QEventLoop, QTimer             # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox   # noqa: E402

from taffy_pet import chat as CH                        # noqa: E402
from taffy_pet import chat_store                        # noqa: E402
from taffy_pet import chat_window as CW                 # noqa: E402
from taffy_pet import config as cfgmod                  # noqa: E402
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
    """按顺序读出消息区里每个气泡/提示的文字（末尾那个弹簧没有控件，跳过）。"""
    out = []
    for i in range(win.msgs.count()):
        w = win.msgs.itemAt(i).widget()
        if w is not None:
            out.append(w.text())
    return out


def reset_handler() -> None:
    """每个用例之前必须重置 —— 这些是类属性，上一个用例改过的会留到下一个。"""
    TC.Handler.status = 200
    TC.Handler.gate = None
    TC.Handler.delay = 0.0
    TC.Handler.no_final_newline = False
    TC.Handler.seen = {}


def run_case(fn) -> None:
    """一个用例崩了别把后面的都带下水：记一笔，重置假接口，接着跑。"""
    reset_handler()                 # 上一个用例改过的类属性不能留到这一个
    try:
        fn()
    except Exception as e:                              # noqa: BLE001
        import traceback
        traceback.print_exc()
        ck(f"{fn.__name__} 不该抛异常", f"{type(e).__name__}: {e}", None)
    finally:
        reset_handler()


def new_window(cfg: dict):
    return CW.ChatWindow(cfg)


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
    # 顺手记一笔 cfgmod.save 有没有在关窗时被调到。几何键存下去也读不回来（那是
    # 已裁决的偏差，config.load() 会把不在 DEFAULTS 里的键丢掉），但「关窗时试图
    # 保存」这件事本身该有人守着 —— 不然哪天 closeEvent 里那行被删了没人知道。
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
    win.show()
    pump(50)
    shown = [t for t in texts(win) if t.strip()]
    ck("重开同一个窗口，消息区就是落盘的那些",
       shown[-len(chat_store.load()):], [m["content"] for m in chat_store.load()])
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
    # 欢迎语 / 第一条 / 第一条的回复 / 第二条，共 4 条 —— 上一轮的气泡没被顶掉
    ck("上一轮的气泡还在，第二条也出来了",
       [t for t in texts(win) if t.strip()],
       ["和 taffy 打个招呼吧喵", "第一条", FULL, "第二条"])

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

    win.pending_text = "攒歪了的字"
    pend.set_text("攒歪了的字")
    win._on_done(FULL)

    ck("气泡被覆盖成 _on_done 收到的文本", pend.text(), FULL)
    ck("落盘的也是那份", chat_store.load()[-1]["content"], FULL)
    ck("两者一致", pend.text(), chat_store.load()[-1]["content"])
    win.close()


def case_menu_entry() -> None:
    """pet.py 的那个临时入口：连点两次只开一个窗口（brief Step 3 的第 4 条）。

    没有 ChatWindow 之外的入口能测到 pet.py 那两行接线，所以顺手在这儿守一下 ——
    不然「和她说话」写错了要等真跑起来才发现。
    """
    print("菜单入口「和她说话」：")
    from taffy_pet.pet import PetWindow                # 只这一个用例要它，懒得放文件头
    try:
        pet = PetWindow(cfgmod.load())
    except BaseException as e:                          # 缺素材时抛的是 SystemExit
        ck("塔菲主窗口建得起来", f"{type(e).__name__}: {e}", None)
        return
    try:
        ck("菜单里有「和她说话」",
           "和她说话" in [a.text() for a in pet.build_menu().actions()], True)
        pet.open_chat()
        first = pet.chat
        ck("开出来的是聊天窗口", type(first).__name__, "ChatWindow")
        pet.open_chat()                                 # 再点一次
        ck("连点两次只有一个窗口", pet.chat is first, True)
        first.close()                                   # closeEvent 也只写临时目录
    finally:
        pet.close()


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
        run_case(case_stop_mid_stream)
        run_case(case_close_mid_stream)
        run_case(case_reopen_backfills)
        run_case(case_clear_history)
        run_case(case_two_sends_same_window)
        run_case(case_on_done_overwrites_bubble)
        run_case(case_quit_while_streaming)
        run_case(case_menu_entry)
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
    sys.exit(_quit_probe() if "--quit-probe" in sys.argv else main())
