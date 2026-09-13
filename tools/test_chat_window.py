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

FULL = "".join(TC.PIECES)          # 假接口会吐的完整回复

# 断言借 test_chat 那份账本（它自己的 main 不会跑，所以里面只会有我们的失败），
# 打印格式和别的手写测试保持一致。
ck = TC.ck
FAILS = TC.FAILS


# ---------- 小工具 ----------
def _isolate_paths(tmp: str) -> None:
    """把会写盘的两个模块指到临时目录。见文件头的说明，这一步不做就会动用户的真数据。"""
    cfgmod.CONFIG_PATH = Path(tmp) / "config.json"
    chat_store.CHAT_PATH = Path(tmp) / "chat.json"
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

    win.btn.click()                # 用户点停止
    gate.set()                     # 放行服务端，让这一轮能正常收尾

    ck("这一轮收尾了", wait_until(lambda: win.worker is None), True)
    part = pend.text()
    ck("留下的是非空的部分文本", bool(part) and part != FULL, True)
    ck("这段确实是完整回复的前缀", FULL.startswith(part), True)
    ck("气泡一直没被撤掉（已吐出的字留着）", win.pending, None)
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
    try:
        win.close()
        crashed = False
    except Exception as e:                              # noqa: BLE001
        crashed = f"{type(e).__name__}: {e}"
    ck("关窗没崩", crashed, False)
    ck("worker 收干净了", win.worker, None)

    # 关完必须再把事件循环转一会儿。worker.wait() 是不处理事件的，那一刻 done 就算
    # 被 emit 了也只是个躺在队列里的信号；这里不转的话「断开没断开」根本测不出来，
    # 断言会变成一句永远正确的话。
    pump(300)
    # _stop_worker 先 disconnect() 再 wait，所以中途断流那次 done 不会回调到窗口上，
    # 那半句自然也不会落盘 —— 界面上它被丢了，落盘里也不该有。反过来说，断开要是
    # 没了，这里就会看到一条凭空多出来的 assistant。
    ck("只有那条 user，没有 assistant",
       [m["role"] for m in chat_store.load()], ["user"])


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
    win = new_window({})
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
    sys.exit(main())
