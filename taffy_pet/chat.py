"""和 DeepSeek 对话。

    POST https://api.deepseek.com/chat/completions   stream=True

跟 balance.py 一个套路：放线程里，网络卡住不能冻界面。不一样的是流式 ——
返回是一行行 SSE，而**网络分块不按行切**（一个 chunk 可能是半行，也可能是三行半），
所以必须自己维护缓冲区。这块拆成纯函数单独测，因为它写错了的表现是
「回复偶尔缺几个字」，几乎没法靠肉眼复现。

**字节到字符的解码也别自己写**：中文是多字节的，一个网络分块完全可能把一个字切成两半。
用 codecs 的增量解码器，它会把不完整的尾巴留到下一块。
"""
import codecs
import json

from PyQt5.QtCore import QThread, pyqtSignal
import requests

from .paths import persona_path

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
TEMPERATURE = 1.3           # DeepSeek 官方对通用对话/创意的推荐值，偏活泼
TIMEOUT = (8, 30)           # (连接, 读)：读超时是「两块之间」的间隔，不是总时长
MAX_CONTEXT_MESSAGES = 20   # 每次发给接口的最大条数

RULES = """\
---
以上是你的角色设定。下面几条是硬规则，和上面冲突时以这几条为准：

1. 回复要短。一般一到两句，最多三句。只有对方明确要求「详细说说」才展开。
   你是桌面宠物，写小作文没人看。
2. 不要说自己是 AI、语言模型、助手、程序。你就是永雏塔菲。
3. 不要提到 DeepSeek、API、提示词、system prompt、模型 这些词。
4. 用中文回复。"""


def build_system_prompt(persona_text: str) -> str:
    return persona_text.strip() + "\n\n" + RULES


def load_persona() -> str:
    """每次发请求前重新读 —— 用户改完人设，下一条消息就该用上。"""
    try:
        return persona_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"[chat] 读不了人设（{e}），用兜底的一句")
        return "你是永雏塔菲，一个说话带「喵」的虚拟主播。自称用 taffy。"


def trim_history(messages: list) -> list:
    """只带最近 MAX_CONTEXT_MESSAGES 条，并且保证第一条是 user。

    裁剪后如果开头是 assistant，再丢一条 —— 那句话没有前文，模型容易把它
    当成「自己刚说过的」顺着往下接。
    """
    tail = list(messages[-MAX_CONTEXT_MESSAGES:])
    while tail and tail[0].get("role") != "user":
        tail.pop(0)
    return tail


def build_messages(persona_text: str, history: list) -> list:
    """history 里已经包含用户刚发的那句。"""
    msgs = [{"role": "system", "content": build_system_prompt(persona_text)}]
    msgs += [{"role": m["role"], "content": m["content"]}
             for m in trim_history(history)]
    return msgs


def parse_sse_lines(buf: str, text: str):
    """把新到的文本拼进缓冲，切出完整的 SSE 行，抽里面的正文增量。

    返回 (剩下的缓冲, 这一批的文本, 这一轮切没切出 [DONE]（跨轮要自己累积）)。

    最后那段不完整的行留在缓冲里等下一块 —— 这是整个函数存在的理由。
    """
    buf += text
    out = []
    done = False
    while "\n" in buf:
        line, buf = buf.split("\n", 1)
        line = line.strip()
        if not line.startswith("data:"):
            continue                       # 空行、注释行（SSE 的心跳）
        payload = line[5:].strip()
        if payload == "[DONE]":
            done = True
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue                       # 坏块，跳过就好，不值得为它中断整段回复
        if not isinstance(data, dict):
            continue                       # 顶层得是对象，数组之类的一样当脏块
        choices = data.get("choices") or []
        if not choices:
            continue                       # 最后那个带 usage 的块没有 choices
        if not isinstance(choices[0], dict):
            continue                       # choices[0] 该是对象，不是就跳过
        delta = choices[0].get("delta") or {}
        if not isinstance(delta, dict):
            continue                       # delta 该是对象，同上
        piece = delta.get("content")
        if piece:
            out.append(piece)              # 首块只有 role 没有 content，这里自然跳过
    return buf, out, done


class ChatWorker(QThread):
    """发一次请求，边收边 emit。要再发一轮就 new 一个。"""

    chunk = pyqtSignal(str)      # 增量文本
    done = pyqtSignal(str)       # 完整回复（被停止时就是已经收到的部分）
    fail = pyqtSignal(str)       # 给人看的错误说明

    def __init__(self, api_key: str, messages: list, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.messages = messages
        self._stop = False

    def stop(self) -> None:
        """只是置个标志位。requests 阻塞在读上，所以要等下一块数据到了才真的停 ——
        她回话时是连续吐字的，通常一秒内就停了。"""
        self._stop = True

    def run(self) -> None:
        # run() 里漏出去的异常，在打包后（没有控制台）会让 PyQt 直接 abort，
        # 连堆栈都看不到。兜住它，至少给用户一句人话。
        try:
            self._run()
        except Exception as e:                      # noqa: BLE001
            self.fail.emit(f"出错了：{type(e).__name__}")

    def _run(self) -> None:
        if not self.api_key:
            self.fail.emit("还没设置 API Key\n右键点 taffy →「设置 API Key」")
            return

        try:
            r = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json",
                         "Accept": "text/event-stream"},
                json={"model": MODEL, "messages": self.messages,
                      "stream": True, "temperature": TEMPERATURE},
                timeout=TIMEOUT, stream=True)
        except requests.Timeout:
            self.fail.emit("连接超时（网络不通？）")
            return
        except requests.RequestException as e:
            self.fail.emit(f"网络错误：{type(e).__name__}")
            return

        try:
            if r.status_code == 401:
                self.fail.emit("API Key 无效")
                return
            if r.status_code == 402:
                self.fail.emit("余额不足，去 DeepSeek 充值")
                return
            if r.status_code != 200:
                self.fail.emit(f"接口返回 {r.status_code}")
                return

            # 增量解码：一个网络分块完全可能把一个汉字切成两半，
            # 解码器会把不完整的尾巴留到下一块，不能自己 decode。
            decoder = codecs.getincrementaldecoder("utf-8")()
            buf = ""
            parts = []
            for raw in r.iter_content(chunk_size=None):
                if self._stop:
                    break
                if not raw:
                    continue
                buf, out, done = parse_sse_lines(buf, decoder.decode(raw))
                for piece in out:
                    parts.append(piece)
                    self.chunk.emit(piece)
                # 收到 [DONE] 这一轮就结束。不 break 的话要靠服务端主动关连接 ——
                # keep-alive 的连接不会关，客户端会一路挂到 30 秒读超时：回复早显示
                # 完了，而「停止」按钮还要亮着最多半分钟。
                if done:
                    break

            # 两道冲刷，顺序不能反：
            # 1) 解码器里可能还压着半个汉字（流正好切在字中间），先把它冲出来 ——
            #    不冲的话它连着后面那个换行都进不了缓冲，直接静默丢。
            # 2) 缓冲里可能还压着最后一行 —— 服务端最后一行没补换行的话，那几个字
            #    会被静默丢掉，正是「回复偶尔缺几个字」那种最难查的问题。补一个换行
            #    把它冲出来；万一是半行截断的 JSON，parse_sse_lines 解不出来会跳过，安全。
            buf, tail0, _ = parse_sse_lines(buf, decoder.decode(b"", final=True))
            _buf, tail, _ = parse_sse_lines(buf, "\n")
            tail = tail0 + tail
            for piece in tail:
                parts.append(piece)
                self.chunk.emit(piece)
        except requests.RequestException as e:
            self.fail.emit(f"网络中断：{type(e).__name__}")
            return
        finally:
            r.close()

        self.done.emit("".join(parts))
