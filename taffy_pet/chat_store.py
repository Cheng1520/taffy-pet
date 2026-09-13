"""聊天记录的读写。

存 %APPDATA%\\TaffyPet\\chat.json（源码运行时在项目根，跟 config.json 一样）。

两个刻意的选择：

- **原子写**：先写 .tmp 再 os.replace。聊天记录每轮都要存一次，写一半崩了文件就废了，
  整个历史都没了。config.json 没这么做是因为它写得很少、内容也能重新填，这里不行。
- **上限 200 条**，超了从最老的丢。不然用一年能涨到好几兆。

读的时候一律「出错就当空的」—— 宁可丢聊天记录，也不能让她起不来。
"""
import json
import os
from datetime import datetime

from .paths import CHAT_PATH, ensure_data_dir

MAX_STORED = 200
VERSION = 1


def make(role: str, content: str) -> dict:
    return {"role": role, "content": content,
            "ts": datetime.now().isoformat(timespec="seconds")}


def _valid(m) -> bool:
    return (isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
            and m["content"])


def load() -> list:
    if not CHAT_PATH.exists():
        return []
    # UnicodeDecodeError 也得接住：用户拿记事本把 chat.json 存成 ANSI 就会抛它，
    # 它既不继承 OSError 也不是 JSONDecodeError，漏出去会在构造函数里把整个程序带崩
    try:
        data = json.loads(CHAT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[chat] 读不了聊天记录（{e}），当空的处理")
        return []
    if not isinstance(data, dict) or data.get("version") != VERSION:
        print("[chat] 聊天记录版本不认，当空的处理")
        return []
    msgs = data.get("messages")
    if not isinstance(msgs, list):
        return []
    # 读侧也要卡上限：save 只管自己写的那些，一份手工改过 / 从别处拷来的 chat.json
    # 会被整份铺出来，而文档写的是「最多 200 条」。超了照样从最老的丢。
    return [m for m in msgs if _valid(m)][-MAX_STORED:]


def save(messages: list) -> None:
    ensure_data_dir()          # 打包之后目录可能还不存在
    tmp = CHAT_PATH.parent / (CHAT_PATH.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps({"version": VERSION, "messages": messages[-MAX_STORED:]},
                       ensure_ascii=False, indent=1),
            encoding="utf-8")
        os.replace(tmp, CHAT_PATH)
    except OSError as e:
        print(f"[chat] 存不了聊天记录：{e}")
        # 那份 .tmp 里装着完整的一份对话内容，不删就一直躺在盘上（下一次 save 才会
        # 盖掉它）。config.save 那边同理，但那边写失败基本只可能是路径不通。
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def clear() -> None:
    try:
        CHAT_PATH.unlink(missing_ok=True)
    except OSError as e:
        print(f"[chat] 删不掉聊天记录：{e}")
