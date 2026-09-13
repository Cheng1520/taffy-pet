"""聊天窗口。

用系统标准窗口，只给标题栏上色（Task 6），不做无边框 —— 无边框的话拖边缩放、
窗口吸附、最小化动画全得自己实现，换来的只是「标题栏颜色可控」，而 Windows 11
的 DwmSetWindowAttribute 本来就能做这件事。
"""
import shutil

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QPixmap
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                             QMessageBox, QPushButton, QScrollArea, QTextEdit,
                             QVBoxLayout, QWidget)

from . import chat as chatmod
from . import chat_store
from . import config as cfgmod
from .paths import ASSETS, PERSONA_DEFAULT, PERSONA_PATH


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


class _PendingLabel(QLabel):
    """临时占位。对外只暴露 set_text，Task 6 换成真气泡时调用方不用改。"""

    def set_text(self, text: str) -> None:
        self.setText(text)


class ChatWindow(QWidget):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.worker = None
        self.pending = None          # 正在流式填充的那个气泡
        self.pending_text = ""       # 这一轮已经吐出来的字，_on_chunk 往里攒
        self.history = chat_store.load()

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
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.area = QWidget()
        self.msgs = QVBoxLayout(self.area)
        self.msgs.setContentsMargins(14, 14, 14, 14)
        self.msgs.setSpacing(10)
        self.msgs.addStretch(1)                  # 消息从上面排起，这个弹簧永远在最末
        self.scroll.setWidget(self.area)
        root.addWidget(self.scroll, 1)

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 10, 14, 12)
        row.setSpacing(8)
        self.input = ChatInput(self.send)
        self.input.setPlaceholderText("和 taffy 说点什么…（回车发送，Shift+回车换行）")
        self.input.setFixedHeight(72)
        row.addWidget(self.input, 1)

        self.btn = QPushButton("发送")
        self.btn.setFixedSize(72, 72)
        self.btn.clicked.connect(self._on_button)
        row.addWidget(self.btn)
        root.addWidget(bar)

    def _at_bottom(self) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _add(self, widget) -> None:
        # 插在弹簧前面，否则新消息会跑到下面去
        self.msgs.insertWidget(self.msgs.count() - 1, widget)

    # ---------- 渲染 ----------
    def _render_history(self) -> None:
        for m in self.history:
            self._append_widget(m["content"], m["role"] == "user")
        if not self.history:
            self._append_notice("和 taffy 打个招呼吧喵")
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _append_widget(self, text: str, mine: bool):
        """Task 6 会换成带头像和尾巴的版本。

        返回的控件上挂一个 `_outer`，指向真正被塞进布局的那个顶层控件 ——
        Task 6 加了头像之后，「返回的气泡」和「插进布局的那一行」就不是同一个了，
        中途要撤销的时候得删对那个。Task 5 里两者是同一个。
        """
        lab = _PendingLabel(text)
        lab.setWordWrap(True)
        lab.setAlignment(Qt.AlignRight if mine else Qt.AlignLeft)
        lab._outer = lab
        self._add(lab)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return lab

    def _append_notice(self, text: str) -> None:
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setAlignment(Qt.AlignCenter)
        self._add(lab)

    # ---------- 发送 ----------
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.worker is not None:
            return
        self.input.clear()
        self.history.append(chat_store.make("user", text))
        self._append_widget(text, True)
        chat_store.save(self.history)
        self._start_reply()

    def _start_reply(self) -> None:
        self.pending_text = ""
        self.pending = self._append_widget("", False)
        self.worker = chatmod.ChatWorker(
            cfgmod.api_key(self.cfg),
            chatmod.build_messages(chatmod.load_persona(), self.history),
            self)
        self.worker.chunk.connect(self._on_chunk)
        self.worker.done.connect(self._on_done)
        self.worker.fail.connect(self._on_fail)
        self.worker.start()
        self._set_busy(True)

    def _on_chunk(self, piece: str) -> None:
        stick = self._at_bottom()
        self.pending_text += piece
        if self.pending is not None:
            self.pending.set_text(self.pending_text)
        if stick:
            self._scroll_to_bottom()

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
            self.worker.terminate()
            self.worker.wait(500)
            if self.worker.isRunning():
                # 没杀干净就绝不能放手。线程还活着而引用丢了，窗口析构时 Qt 照样 abort
                # （见 __init__ 里那段）。宁可把引用留着，等下一次收尾
                # （关窗 / aboutToQuit）再试一次。
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

        （Task 6 之前的临时版：先只保证文件到位。）
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
