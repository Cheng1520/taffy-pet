r"""她会用的工具 —— 桌宠和「只会聊天的套壳」之间的那条线。

现在的桌宠**什么都不知道也什么都做不了**：不知道今天几号、现在几点，
说过的事下一轮就忘。人设里那条「累了就把自己扔床上喵」全靠猜，你说「现在才几点」
她也接不上。这一层给她两个最小但真的用得上的能力：

| 工具       | 作用                                   |
| ---------- | -------------------------------------- |
| `remember` | 把一件事写进长期记忆（`memory.md`）    |
| `now`      | 知道现在的日期和时间                   |

### 函数名为什么是英文

`name` 不是给人看的，是 API 的**标识符**，OpenAI 那套格式要求
`^[a-zA-Z0-9_-]{1,64}$` —— 中文名会被接口直接拒掉。
给人看的部分是 `description`，那个写中文没问题，模型就是靠它决定调不调。

### 这一层的硬规矩

**`run()` 绝不抛异常。** 它跑在人机之间：它炸了，整轮对话就没了。
任何工具出错都要变成一句能回填给模型的话 —— 模型看到「没记住」还能跟用户解释，
看到崩溃就只会把整轮请求带下水。
"""

from . import memory

# 每轮对话最多让她连续调几次工具。到顶就用已经拿到的内容收尾。
#
# 有上限是因为「模型反复调同一个工具」是真实会发生的（尤其记忆写失败时它会重试）。
# 不封顶的话这一轮就永远不结束，界面上「停止」按钮一直亮着。
MAX_ROUNDS = 3

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            # 描述里写清「什么时候该调」比写「这个函数做什么」有用得多，
            # 模型是按场景匹配的。
            "description": "把关于用户的一件事写进长期记忆，以后每次对话都记得。"
                           "用户说了自己的偏好、习惯、正在做的事、重要的人或日期时调用。"
                           "不要记无关紧要的闲聊，也不要记你自己说过的话。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "要记住的事，一句话，用第三人称写。"
                                       "比如「用户在做塔菲桌宠」「用户经常熬夜到两点」。",
                    },
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "now",
            "description": "查现在的日期和时间。用户提到今天、明天、几点、"
                           "该不该睡了、多少天之后这类跟时间有关的话时调用 —— "
                           "你自己是不知道当前时间的，必须查。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_BY_NAME = {t["function"]["name"]: t for t in TOOLS}


def parse_args(raw):
    r"""把模型给的 `arguments` 解析成 dict。

    它按协议是个 **JSON 字符串**，但实际拿到的东西什么形状都有：已经是 dict 的、
    空串、半截 JSON、纯文本。这里一律不抛 —— 解析不出来就给空 dict，
    让工具自己报「参数不对」，比在这儿炸掉强。
    """
    import json
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        got = json.loads(raw)
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def names() -> list:
    return [t["function"]["name"] for t in TOOLS]


def _now() -> str:
    from datetime import datetime
    n = datetime.now()
    hour = n.hour
    if hour < 5:
        part = "凌晨"
    elif hour < 9:
        part = "早上"
    elif hour < 12:
        part = "上午"
    elif hour < 14:
        part = "中午"
    elif hour < 18:
        part = "下午"
    elif hour < 23:
        part = "晚上"
    else:
        part = "深夜"
    return (f"现在是 {n.year} 年 {n.month} 月 {n.day} 日 "
            f"{n.hour:02d}:{n.minute:02d}（{part}，星期{'一二三四五六日'[n.weekday()]}）")


def run(name: str, args) -> str:
    r"""执行一个工具调用，返回要回填给模型的一段文字。

    `args` 是模型生成的参数，形状不可控（可能是 dict、可能是半截 JSON 字符串、
    也可能是 None）。这里**每一步都要假设它不对** —— 那些都不是异常，
    是正常会发生的情况。
    """
    try:
        if name not in _BY_NAME:
            return f"没有叫 {name} 的工具"
        if name == "remember":
            if not isinstance(args, dict):
                return "没记住：参数格式不对"
            return memory.add(args.get("fact", ""))
        if name == "now":
            return _now()
        return f"工具 {name} 没有实现"
    except Exception as e:                      # noqa: BLE001
        # 工具炸了不能把整轮对话带下水，变成一句话回填给模型。
        print(f"[agent] 工具 {name} 出错：{type(e).__name__}: {e}")
        return f"工具出错了（{type(e).__name__}）"
