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

    返回 (剩下的缓冲, 这一批的文本, 是否收到 [DONE])。

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
        choices = data.get("choices") or []
        if not choices:
            continue                       # 最后那个带 usage 的块没有 choices
        delta = choices[0].get("delta") or {}
        piece = delta.get("content")
        if piece:
            out.append(piece)              # 首块只有 role 没有 content，这里自然跳过
    return buf, out, done
