"""带网络的聊天测试：起一个本地假接口，验证流式解析和线程行为。

    python tools/test_chat.py

真接口要 Key 要网，测不了。这里起一个只回 SSE 的本地 HTTP 服务，把 API_URL 指过去，
就能测到真实网络路径上的东西：任意分块、跨块的行、取消、各种错误码。

用 Qt 事件循环跑，因为 ChatWorker 是 QThread。
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 本机装了 Watt Toolkit，系统代理（Windows 注册表）会被 requests 认下来，
# 连发往 127.0.0.1 的请求都会被劫持成 404。这里绕开它。
# 只改测试，不动产品代码 —— ChatWorker 对着真接口走代理是对的，跟 balance.py 一样。
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QEventLoop, QTimer            # noqa: E402
from PyQt5.QtWidgets import QApplication               # noqa: E402

from taffy_pet import chat as CH                       # noqa: E402

PIECES = ["在", "呢", "在呢，", "怎么啦喵"]
FAILS = []


def ck(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f"  期望 {want!r}"))
    if not ok:
        FAILS.append(name)


GATE_TIMEOUT = 10.0        # 服务端等放行的安全阀。必须比客户端的 8 秒超时长 ——
                           # 短了的话「整份读完再吐」的突变等服务端自己超时放行后就变绿了


class Handler(BaseHTTPRequestHandler):
    status = 200
    delay = 0.0
    no_final_newline = False  # 末行不补尾换行、也不发 [DONE]（末行冲刷那个用例）
    gate = None               # 发完第一行后卡在这个 Event 上（真流式那个用例）
    seen = {}                 # 收到的请求，留给测试断言
    protocol_version = "HTTP/1.1"              # 不开这个的话 body 会一次读完，分块测不到

    def log_message(self, *a):
        pass                                   # 别把请求日志刷到屏幕上

    def _body(self) -> bytes:
        """拼 SSE 响应体。no_final_newline 时末行不补换行也不发 [DONE] ——
        那一行会留在 ChatWorker 的缓冲里，只有流结束时的强制冲刷才捞得出来。"""
        out = b""
        for i, p in enumerate(PIECES):
            line = "data: " + json.dumps({"choices": [{"delta": {"content": p}}]},
                                         ensure_ascii=False)
            if self.no_final_newline and i == len(PIECES) - 1:
                out += line.encode("utf-8")    # 末行故意不补换行
            else:
                out += (line + "\n\n").encode("utf-8")
        if not self.no_final_newline:
            out += b"data: [DONE]\n\n"
        return out

    def _write_chunked(self, data: bytes) -> bool:
        """故意每 7 字节切一刀 —— 保证切在汉字中间，逼出增量解码的问题。
        返回 False 表示客户端已经走了，不用再写。"""
        for i in range(0, len(data), 7):
            part = data[i:i + 7]
            try:
                self.wfile.write(b"%X\r\n" % len(part) + part + b"\r\n")
                self.wfile.flush()
            except (ConnectionAbortedError, BrokenPipeError,
                    ConnectionResetError):
                return False                   # 客户端提前关了，正常
            if self.delay:
                time.sleep(self.delay)
        return True

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        # 请求体不能读了就丢：模型、温度、stream、鉴权头都是计划的硬约束，
        # 假接口不记下来就没人守着它们了。
        try:
            Handler.seen = {"json": json.loads(raw.decode("utf-8")),
                            "auth": self.headers.get("Authorization")}
        except (ValueError, UnicodeDecodeError):
            Handler.seen = {"json": {}, "auth": self.headers.get("Authorization")}

        if self.status != 200:
            body = b'{"error":"stub"}'
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        body = self._body()
        if self.gate is not None:
            # 第一行发完就卡住，等测试收到首块后放行。只有真·边收边吐的客户端
            # 才可能让测试收到首块；整份读完再解析的实现会一路卡到超时。
            cut = body.index(b"\n\n") + 2
            if not self._write_chunked(body[:cut]):
                return
            self.gate.wait(timeout=GATE_TIMEOUT)
            rest = body[cut:]
        else:
            rest = body                        # 不分段，保持原来的分块行为
        if rest and not self._write_chunked(rest):
            return
        try:
            self.wfile.write(b"0\r\n\r\n")      # chunked 的结束块
            self.wfile.flush()
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass


class QuietServer(ThreadingHTTPServer):
    """默认的 handle_error 会把处理请求时的 traceback 打到 stderr。测试里客户端提前
    断开（取消用例、keep-alive 收尾）是正常现象，不该刷一屏红字把真正的失败淹掉。"""

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):
            return                              # 客户端断开：正常，静默
        super().handle_error(request, client_address)


def serve():
    srv = QuietServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1/chat"


class _FakeResponse:
    """最小可用的 requests 响应替身。

    真接口测不到两件事，这两件都只能靠它：
    - 流被提前 break 时 response 有没有关掉（用户点「停止」走的就是这条）；
    - 服务端发完 [DONE] 之后**不复用连接也不关连接**（keep-alive 的常见形态）时，
      客户端会不会一直等到读超时。
    """

    status_code = 200

    def __init__(self, chunks, per_chunk_sleep=0.0, tail_sleep=0.0):
        self._chunks = chunks
        self._per_chunk_sleep = per_chunk_sleep
        self._tail_sleep = tail_sleep
        self.closed = False
        self.waited_past_done = False

    def iter_content(self, chunk_size=None):
        for c in self._chunks:
            if self._per_chunk_sleep:
                time.sleep(self._per_chunk_sleep)
            yield c
        # 走到这儿说明 [DONE] 之后客户端还想要下一块 —— 也就是没 break，在等连接
        self.waited_past_done = True
        if self._tail_sleep:
            time.sleep(self._tail_sleep)

    def close(self):
        self.closed = True


def _sse(text: str) -> bytes:
    return ("data: " + json.dumps({"choices": [{"delta": {"content": text}}]},
                                  ensure_ascii=False) + "\n\n").encode("utf-8")


def run_worker(worker, timeout_ms=8000, stop_after_ms=None, on_chunk=None):
    """跑一个 ChatWorker 等它结束，把三个信号收下来。

    on_chunk 会在每收到一个增量之后被叫一次（信号是队列连接，跑在主线程里）。
    """
    loop = QEventLoop()
    got = {"chunks": [], "done": None, "fail": None}

    def take_chunk(text):
        got["chunks"].append(text)
        if on_chunk is not None:
            on_chunk()

    worker.chunk.connect(take_chunk)
    worker.done.connect(lambda t: (got.__setitem__("done", t), loop.quit()))
    worker.fail.connect(lambda t: (got.__setitem__("fail", t), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    if stop_after_ms is not None:
        QTimer.singleShot(stop_after_ms, worker.stop)
    worker.start()
    loop.exec_()
    worker.wait(3000)
    return got


def main() -> int:
    app = QApplication(sys.argv)               # noqa: F841  QThread 需要它
    srv, url = serve()
    CH.API_URL = url                           # 所有请求都打到假接口上
    try:
        print("正常流式：")
        Handler.seen = {}
        got = run_worker(CH.ChatWorker("sk-test", [{"role": "user", "content": "在吗"}]))
        ck("拼起来是完整回复", got["done"], "".join(PIECES))
        ck("增量拼起来也一样", "".join(got["chunks"]), "".join(PIECES))
        ck("没有增量分片丢失", len(got["chunks"]) >= 1, True)
        ck("没有报错", got["fail"], None)

        print("发出去的请求：")
        sent = Handler.seen.get("json") or {}
        ck("模型是 deepseek-chat", sent.get("model"), "deepseek-chat")
        ck("temperature 是 1.3", sent.get("temperature"), 1.3)
        ck("开着流式", sent.get("stream"), True)
        ck("带上了 Key", Handler.seen.get("auth"), "Bearer sk-test")

        print("真流式（边收边吐）：")
        gate = threading.Event()
        Handler.gate = gate
        got = run_worker(CH.ChatWorker("sk-test", [{"role": "user", "content": "x"}]),
                         on_chunk=gate.set)
        released = gate.is_set()
        Handler.gate = None
        # 服务端卡在第一行之后不动，只有 ChatWorker 在流结束之前就吐了字，
        # 测试才收得到首块、才放得了行。整份读完再解析会一路卡到 8 秒超时。
        ck("流没结束就吐了首块", released, True)
        ck("放行后仍拼出完整回复", got["done"], "".join(PIECES))
        ck("没报错", got["fail"], None)

        print("末行不补尾换行：")
        Handler.no_final_newline = True
        got = run_worker(CH.ChatWorker("sk-test", [{"role": "user", "content": "x"}]))
        Handler.no_final_newline = False
        # 末行没有换行就留在 ChatWorker 的缓冲里，没有流结束时的强制冲刷就会少几个字。
        ck("末行的字没被静默丢掉", got["done"], "".join(PIECES))
        ck("增量也一样", "".join(got["chunks"]), "".join(PIECES))
        ck("没报错", got["fail"], None)

        print("错误码：")
        for code, want in [(401, "API Key 无效"), (402, None), (500, None)]:
            Handler.status = code
            got = run_worker(CH.ChatWorker("sk-test", [{"role": "user", "content": "x"}]))
            if want:
                ck(f"{code}", got["fail"], want)
            else:
                ck(f"{code} 有提示", bool(got["fail"]), True)
            ck(f"{code} 不吐内容", got["done"], None)
        Handler.status = 200

        print("没填 Key：")
        got = run_worker(CH.ChatWorker("", [{"role": "user", "content": "x"}]))
        ck("直接报错不发请求", "API Key" in (got["fail"] or ""), True)

        print("取消：")
        Handler.delay = 0.08                   # 让回复慢到有时间点停止
        got = run_worker(CH.ChatWorker("sk-test", [{"role": "user", "content": "x"}]),
                         stop_after_ms=2000)
        Handler.delay = 0.0
        ck("停止后能收尾", got["done"] is not None or got["fail"] is not None, True)
        ck("拿到的是部分回复", len(got["done"] or "") < len("".join(PIECES)), True)
        print(f"  ok   停在了 {len(got['done'] or '')} 个字（一共 {len(''.join(PIECES))} 个）")

        print("提前停止时关掉 response：")
        # 「每条路径都必须关掉 response」是计划的 Global Constraint，而用户点「停止」
        # 走的正是「提前 break 出流式循环」这条。改前完全没有覆盖：把 chat.py 里
        # `finally: r.close()` 整段删掉，两套测试照样全绿。
        fake = _FakeResponse([_sse(p) for p in PIECES] + [b"data: [DONE]\n\n"],
                             per_chunk_sleep=0.08)
        real_post = CH.requests.post
        CH.requests.post = lambda *a, **k: fake
        try:
            got = run_worker(CH.ChatWorker("sk-test", [{"role": "user", "content": "x"}]),
                             stop_after_ms=120)
        finally:
            CH.requests.post = real_post
        ck("这一轮收尾了", got["done"] is not None or got["fail"] is not None, True)
        # 这条是上面那条的前提：整段都吐完了的话走的是自然结束，压根没 break
        ck("确实是提前停下的", len(got["done"] or "") < len("".join(PIECES)), True)
        ck("response 被关掉了", fake.closed, True)

        print("收到 [DONE] 就收尾：")
        # 服务端发完 [DONE] 之后既不复用也不关连接（keep-alive 很常见）。不 break 的话
        # 客户端会一直挂到 30 秒读超时 —— 回复早显示完了，「停止」按钮还要亮半分钟。
        fake2 = _FakeResponse([_sse(p) for p in PIECES] + [b"data: [DONE]\n\n"],
                              tail_sleep=0.4)
        real_post = CH.requests.post
        CH.requests.post = lambda *a, **k: fake2
        try:
            got = run_worker(CH.ChatWorker("sk-test", [{"role": "user", "content": "x"}]))
        finally:
            CH.requests.post = real_post
        ck("[DONE] 之后没再要下一块", fake2.waited_past_done, False)
        # break 不能把已经吐出来的字弄丢 —— 这条是上一条的安全带
        ck("提前收尾之后回复还是完整的", got["done"], "".join(PIECES))
        ck("没报错", got["fail"], None)
    finally:
        srv.shutdown()

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
