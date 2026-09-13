"""启动桌宠。

    python  main.py      有控制台，出问题看得到 —— 调 bug 用这个
    pythonw main.py      桌面快捷方式走的是这个，不弹黑框

桌面图标由 tools/make_shortcut.py 创建。
"""
import os
import sys
import traceback

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import QApplication, QMessageBox

from taffy_pet import config as cfgmod
from taffy_pet.paths import LOG_PATH as LOG, ensure_data_dir
from taffy_pet.pet import PetWindow

# 同名 QLocalServer 只能有一个，第二个进程 listen 会失败 —— 拿它判断
# 「是不是已经有一只塔菲在跑了」。双击桌面图标最容易犯的错就是连点两下，
# 起出两只一模一样的塔菲叠在一起，肉眼还看不出来。
SERVER_NAME = "taffy-pet-single-instance"


def _quiet_broken_output() -> None:
    """pythonw 双击启动时没有控制台，标准输出可能是个坏句柄。

    坏句柄上任何一次 print 都会抛 OSError，而 PyQt 里槽函数抛异常会直接 abort ——
    也就是说「设置 API Key」里那一句日志能把整个程序带走。宁可丢进 devnull。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            continue
        try:
            stream.flush()
        except Exception:      # 坏句柄抛 OSError，已关闭的抛 ValueError，都算数
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _fatal(msg: str) -> None:
    """起不来的时候必须让人看见 —— pythonw 下「双击没反应」什么线索都不留。"""
    try:
        ensure_data_dir()
        LOG.write_text(msg, encoding="utf-8")
    except OSError:
        pass
    if QApplication.instance() is not None:
        QMessageBox.critical(None, "塔菲起不来", f"{msg}\n\n详情写在 {LOG}")
    else:
        print(msg, file=sys.stderr)


def _on_uncaught(exc_type, exc, tb) -> None:
    _fatal("".join(traceback.format_exception(exc_type, exc, tb)))


def _wake_existing() -> bool:
    """已经有一只在跑？戳她一下让她蹦蹦，然后第二个进程自己退出。"""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(200):
        return False
    sock.write(b"ping")
    sock.flush()
    sock.waitForBytesWritten(200)
    sock.disconnectFromServer()
    return True


def _greet(pet: PetWindow) -> None:
    pet.animator.pounce()
    pet.toast.show_message("我在这儿呢～")
    pet.toast.anchor_above(pet.frameGeometry())


def main() -> int:
    _quiet_broken_output()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # 气泡单独关掉时别把程序带走
    sys.excepthook = _on_uncaught

    if _wake_existing():
        print("[塔菲] 已经有一只在跑了，让她蹦了一下就退出")
        return 0

    ensure_data_dir()
    cfg = cfgmod.load()
    try:
        pet = PetWindow(cfg)
    except BaseException as e:             # SystemExit 也是 BaseException，别漏掉
        _fatal(str(e))
        return 1
    pet.show()

    server = QLocalServer(app)             # 挂在 app 上，否则会被回收掉
    server.removeServer(SERVER_NAME)       # 上次崩了可能留下残名
    if server.listen(SERVER_NAME):
        server.newConnection.connect(lambda: _greet(pet))
    else:
        print("[塔菲] 单实例的名字没占上（不影响使用，只是可能起出第二只）")

    # 等窗口真正摆好位置再弹提示，否则气泡会按还没定位的坐标算
    QTimer.singleShot(600, pet.show_hint_if_first_run)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
