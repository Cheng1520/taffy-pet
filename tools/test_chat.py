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


class Handler(BaseHTTPRequestHandler):
    status = 200
    delay = 0.0
    protocol_version = "HTTP/1.1"              # 不开这个的话 body 会一次读完，分块测不到

    def log_message(self, *a):
        pass                                   # 别把请求日志刷到屏幕上

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.status != 200:
            # keep-alive 下不写 Content-Length 也不关连接，客户端会一直读到读超时。
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
        body = b""
        for p in PIECES:
            body += ("data: " + json.dumps(
                {"choices": [{"delta": {"content": p}}]},
                ensure_ascii=False) + "\n\n").encode("utf-8")
        body += b"data: [DONE]\n\n"
        # 故意每 7 字节切一刀 —— 保证切在汉字中间，逼出增量解码的问题
        for i in range(0, len(body), 7):
            part = body[i:i + 7]
            try:
                self.wfile.write(b"%X\r\n" % len(part) + part + b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return                          # 客户端提前关了，正常
            if self.delay:
                time.sleep(self.delay)
        try:
            self.wfile.write(b"0\r\n\r\n")      # chunked 的结束块
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1/chat"


def run_worker(worker, timeout_ms=8000, stop_after_ms=None):
    """跑一个 ChatWorker 等它结束，把三个信号收下来。"""
    loop = QEventLoop()
    got = {"chunks": [], "done": None, "fail": None}
    worker.chunk.connect(got["chunks"].append)
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
        got = run_worker(CH.ChatWorker("sk-test", [{"role": "user", "content": "在吗"}]))
        ck("拼起来是完整回复", got["done"], "".join(PIECES))
        ck("增量拼起来也一样", "".join(got["chunks"]), "".join(PIECES))
        ck("没有增量分片丢失", len(got["chunks"]) >= 1, True)
        ck("没有报错", got["fail"], None)

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
