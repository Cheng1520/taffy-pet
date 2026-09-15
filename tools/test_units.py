"""不依赖窗口的纯函数测试。

    python tools/test_units.py

需要鼠标和网络的部分没法自动测：真实点击、右键菜单弹窗、DeepSeek 接口。
那几处靠 tools/smoke.py 和手动验证。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 控制台默认是 GBK 时，打印「¥」「（余额不足）」这类字符会抛 UnicodeEncodeError，
# 被 run_case 接住后报成「test_balance 不该抛异常」—— **假的失败**，代码本身没问题。
# 直接把这个进程的 stdout 掰成 UTF-8，别要求用户记得加 PYTHONUTF8=1。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from taffy_pet import anim as A            # noqa: E402
from taffy_pet import config as C          # noqa: E402
from taffy_pet.balance import format_balance  # noqa: E402

FAILS = []


def ck(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f"  期望 {want!r}"))
    if not ok:
        FAILS.append(name)


def test_balance() -> None:
    print("余额解析：")
    ck("人民币", format_balance({"is_available": True, "balance_infos": [
        {"currency": "CNY", "total_balance": "110.00",
         "granted_balance": "10", "topped_up_balance": "100"}]}), "¥110.00")
    ck("美元", format_balance({"is_available": True, "balance_infos": [
        {"currency": "USD", "total_balance": "5.50"}]}), "$5.50")
    ck("多币种", format_balance({"is_available": True, "balance_infos": [
        {"currency": "CNY", "total_balance": "1.00"},
        {"currency": "USD", "total_balance": "2.00"}]}), "¥1.00  $2.00")
    ck("不可用", format_balance({"is_available": False, "balance_infos": [
        {"currency": "CNY", "total_balance": "0.00"}]}), "¥0.00（余额不足）")
    ck("空列表", format_balance({"is_available": True, "balance_infos": []}),
       "余额：0.00")
    # is_available 缺失按「不可用」处理 —— 响应不完整时别谎报余额为 0
    ck("字段缺失", format_balance({}), "余额：无可用账户")


def test_apikey() -> None:
    print("API Key 优先级：")
    cfg = {"api_key": "from_file"}
    os.environ["DEEPSEEK_API_KEY"] = "from_env"
    try:
        ck("环境变量优先", C.api_key(cfg), "from_env")
        ck("来源标注", C.api_key_source(cfg), "环境变量 DEEPSEEK_API_KEY")
    finally:
        del os.environ["DEEPSEEK_API_KEY"]
    ck("回落到文件", C.api_key(cfg), "from_file")
    ck("来源标注", C.api_key_source(cfg), "config.json")
    ck("两边都空", C.api_key({"api_key": "   "}), "")
    ck("未设置", C.api_key_source({"api_key": ""}), "未设置")


def test_persona_path() -> None:
    print("人设路径：")
    from taffy_pet import paths as P
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        fake_user = Path(d) / "persona.md"
        old_user, old_default = P.PERSONA_PATH, P.PERSONA_DEFAULT
        P.PERSONA_PATH, P.PERSONA_DEFAULT = fake_user, P.PERSONA_DEFAULT
        try:
            ck("用户没改过就用默认版", P.persona_path(), P.PERSONA_DEFAULT)
            fake_user.write_text("测试人设", encoding="utf-8")
            ck("用户改过就用用户的", P.persona_path(), fake_user)
        finally:
            P.PERSONA_PATH, P.PERSONA_DEFAULT = old_user, old_default


def test_chat_store() -> None:
    print("聊天记录：")
    from taffy_pet import chat_store as CS
    from pathlib import Path
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        old = CS.CHAT_PATH
        CS.CHAT_PATH = Path(d) / "chat.json"
        try:
            ck("空目录读出来是空的", CS.load(), [])

            msgs = [CS.make("user", "在吗"), CS.make("assistant", "在呢喵")]
            CS.save(msgs)
            back = CS.load()
            ck("存的条数", len(back), 2)
            ck("角色", [m["role"] for m in back], ["user", "assistant"])
            ck("内容", [m["content"] for m in back], ["在吗", "在呢喵"])
            ck("读了能直接发给接口", [(m["role"], m["content"]) for m in back],
               [("user", "在吗"), ("assistant", "在呢喵")])

            # 超过上限从最老的截断
            many = [CS.make("user", f"第{i}条") for i in range(CS.MAX_STORED + 50)]
            CS.save(many)
            kept = CS.load()
            ck("截断到上限", len(kept), CS.MAX_STORED)
            ck("留下的是最新的", kept[-1]["content"], f"第{CS.MAX_STORED + 49}条")

            # 读侧也要卡上限：save 只管自己写的那些，一份手工改过 / 从别处拷来的
            # chat.json 会被整份铺出来，而文档写的是「最多 200 条」。
            # 直接写文件、不走 save —— 走 save 的话截断在写的那一头就发生过了，
            # 读侧那道限制删掉也照样绿（一条恒真的摆设）。
            CS.CHAT_PATH.write_text(json.dumps(
                {"version": CS.VERSION,
                 "messages": [CS.make("user", f"外来{i}") for i in range(CS.MAX_STORED + 30)]},
                ensure_ascii=False), encoding="utf-8")
            ck("读的时候也截到上限", len(CS.load()), CS.MAX_STORED)
            ck("读侧留的也是最新的", CS.load()[-1]["content"],
               f"外来{CS.MAX_STORED + 29}")

            # 坏文件不能让程序起不来
            CS.CHAT_PATH.write_text("{不是合法 json", encoding="utf-8")
            ck("坏文件当空的处理", CS.load(), [])
            # 载荷里必须**放一条真消息**。放空的话 load() 返回 [] 跟版本检查毫无关系，
            # 断言就成了同义反复 —— 实测把版本检查削成 `if not isinstance(data, dict)`
            # 之后它是全绿通过的，而那是「将来升级格式不炸」的唯一防线。
            CS.CHAT_PATH.write_text(
                '{"version": 999, "messages": [{"role": "user", "content": "旧版本的字"}]}',
                encoding="utf-8")
            ck("版本不认当空的处理", CS.load(), [])
            CS.CHAT_PATH.write_text('{"version": 1, "messages": [{"role":"x"},'
                                    '{"role":"user","content":""},'
                                    '{"role":"user","content":"好的"}]}',
                                    encoding="utf-8")
            ck("丢掉残缺的条目", [m["content"] for m in CS.load()], ["好的"])
            # 记事本另存为 ANSI/GBK 是最常见的一种坏法：字节不是合法 UTF-8，
            # read_text 直接抛 UnicodeDecodeError，它不继承 OSError，必须单独接住
            CS.CHAT_PATH.write_bytes("聊天".encode("gbk"))
            ck("GBK 文件当空的处理", CS.load(), [])

            CS.clear()
            ck("清空之后", CS.load(), [])
            CS.clear()   # 再清一次不能炸（文件已经不在了）
            print("  ok   重复清空不报错")
        finally:
            CS.CHAT_PATH = old


def test_chat_prompt() -> None:
    print("人设拼装：")
    from taffy_pet import chat as CH

    p = CH.build_system_prompt("你是塔菲。")
    ck("人设在最前面", p.startswith("你是塔菲。"), True)
    ck("带上了回复要短这条", "回复要短" in p, True)
    ck("带上了不许自称 AI", "不要说自己是 AI" in p, True)
    ck("带上了不许提 DeepSeek", "不要提到 DeepSeek" in p, True)


def test_load_persona() -> None:
    print("人设读取：")
    from taffy_pet import chat as CH
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "persona.md"
        old = CH.persona_path
        CH.persona_path = lambda: p
        try:
            p.write_text("你是塔菲。", encoding="utf-8")
            ck("正常读得到", "你是塔菲。" in CH.load_persona(), True)

            # 用户拿记事本存成 ANSI/GBK —— 这是这个功能最可能出的岔子
            p.write_bytes("你是塔菲。".encode("gbk"))
            ck("GBK 存盘走兜底不崩", "永雏塔菲" in CH.load_persona(), True)

            p.unlink()
            ck("文件没了也走兜底", "永雏塔菲" in CH.load_persona(), True)
        finally:
            CH.persona_path = old


def test_chat_trim() -> None:
    print("历史裁剪：")
    from taffy_pet import chat as CH

    def m(role, n):
        return {"role": role, "content": f"{role}{n}"}

    ck("空历史不炸", CH.trim_history([]), [])

    short = [m("user", 1), m("assistant", 2)]
    ck("不够长就全带上", len(CH.trim_history(short)), 2)

    # 40 条交替，裁剪后应该只剩最后 20 条
    long = [m("user" if i % 2 == 0 else "assistant", i) for i in range(40)]
    got = CH.trim_history(long)
    ck("裁到上限", len(got), CH.MAX_CONTEXT_MESSAGES)
    ck("留的是最新的", got[-1]["content"], "assistant39")

    # 裁完开头是 assistant 的时候，再丢一条 —— 那句话没前文，
    # 模型容易当成「自己刚说过的」顺着往下接
    odd = [m("assistant", i) if i % 2 == 0 else m("user", i) for i in range(40)]
    got = CH.trim_history(odd)
    ck("首条必须是 user", got[0]["role"], "user")
    ck("留的是最新的（另一边）", got[-1]["content"], "user39")

    msgs = CH.build_messages("人设", short)
    ck("system 在最前", msgs[0]["role"], "system")
    ck("system 内容", msgs[0]["content"], CH.build_system_prompt("人设"))
    ck("后面是历史", [x["role"] for x in msgs[1:]], ["user", "assistant"])
    ck("只带 role 和 content", sorted(msgs[1].keys()), ["content", "role"])


def test_sse_parse() -> None:
    print("SSE 解析：")
    from taffy_pet import chat as CH

    def data(t):
        return f'data: {{"choices":[{{"delta":{{"content":"{t}"}}}}]}}\n\n'

    # 一次给一整块的正常情况
    buf, out, done = CH.parse_sse_lines("", data("在") + data("呢") + "data: [DONE]\n\n")
    ck("整块", (out, done), (["在", "呢"], True))

    # 跨块：切在半个 JSON 中间
    raw = data("在") + data("呢")
    buf, out, done = CH.parse_sse_lines("", raw[:20])
    ck("半行不吐", out, [])
    buf, out2, done = CH.parse_sse_lines(buf, raw[20:])
    ck("补齐后吐出来", out2, ["在", "呢"])

    # 逐字节喂 —— 最狠的一种切法，模拟网络任意分块
    # done 要跟 acc 一样跨轮累积：它只表示「这一轮切出了 [DONE]」，
    # 而 [DONE] 那一行是被倒数第二个字符（换行）补完的，最后一轮吃的是空行，
    # 只看最后一轮的 done 永远是 False
    buf, acc, saw_done = "", [], False
    for ch in raw + "data: [DONE]\n\n":
        buf, out, done = CH.parse_sse_lines(buf, ch)
        acc += out
        saw_done = saw_done or done
    ck("逐字节喂结果一样", acc, ["在", "呢"])
    ck("逐字节也能收到 DONE", saw_done, True)

    # 各种脏东西都不能炸
    for name, feed in [
        ("空 delta", 'data: {"choices":[{"delta":{}}]}\n\n'),
        ("没有 choices", 'data: {"usage":{"total_tokens":9}}\n\n'),
        ("choices 是空表", 'data: {"choices":[]}\n\n'),
        ("坏 JSON", 'data: {不是 json\n\n'),
        ("顶层是数组", 'data: [1,2,3]\n\n'),
        ("choices[0] 不是对象", 'data: {"choices":["x"]}\n\n'),
        ("delta 不是对象", 'data: {"choices":[{"delta":"x"}]}\n\n'),
        ("非 data 行", ': keep-alive\n\n'),
        ("空行", '\n\n'),
    ]:
        try:
            buf, out, done = CH.parse_sse_lines("", feed)
            ck(name, (out, done), ([], False))
        except Exception as e:                      # noqa: BLE001
            ck(name, f"炸了 {type(e).__name__}", "不该炸")


def test_bounce() -> None:
    print("弹跳曲线：")
    curve = A.bounce_curve()
    lo = min(sy for _, sy, _ in curve)
    hi = max(sy for _, sy, _ in curve)
    ck("起点静止", (round(curve[0][1], 6), round(curve[-1][1], 6)), (1.0, 1.0))
    print(f"  ok   幅度：压扁 {1-lo:.1%} / 拉伸 {hi-1:.1%}")
    if 1 - lo < 0.05:
        FAILS.append("压扁幅度不足 5%")
        print("  FAIL 压扁幅度不足 5%，肉眼看不出来")
    if hi > 1.05:
        FAILS.append("拉伸超过 5%")
        print("  FAIL 拉伸超过 5%，窗口边距会不够")
    # 曲线必须连续，否则每帧之间会跳
    jumps = [abs(curve[i + 1][1] - curve[i][1]) for i in range(len(curve) - 1)]
    print(f"  ok   相邻帧最大跳变 {max(jumps):.5f}（应远小于 0.01）")
    if max(jumps) > 0.01:
        FAILS.append("曲线不连续")
        print("  FAIL 曲线有跳变，关键帧插值断了")


def test_dance() -> None:
    r"""跳舞：帧号怎么走、循环、收尾，以及跳舞期间**不吃**呼吸/弹跳/眨眼。

    这一段是全套测试里唯一建 `PetAnimator` 的地方 —— 别处都走 `bounce_curve()`
    那个纯函数。QObject 不需要 QApplication（QWidget 才需要），所以不用起窗口。
    """
    print("跳舞：")
    a = A.PetAnimator()
    ck("没跳时帧号是 None", a.state()[-1], None)
    ck("没跳时 dancing 是假", a.dancing, False)

    a.dance(0)
    ck("给 0 帧不启动", a.dancing, False)

    # 4 帧、10fps、跳 2 遍 —— 每帧 5 个 tick（tick 是 20ms），一共 40 个。
    a.dance(4, fps=10.0, loops=2)
    ck("开始跳了", a.dancing, True)
    ck("第一帧是 0", a.state()[-1], 0)
    ck("还没跳完", a.bouncing, False)

    # 上限是防「这个舞永远跳不完」—— 真出那种 bug 时，没有上限的话这里会
    # 死循环，测试挂在那儿不报错，比报错难查得多。（第一版就踩了：
    # 收尾判断用了取模后的帧号，那个值永远到不了总帧数。）
    seen = []
    for _ in range(200):
        if not a.dancing:
            break
        sx, sy, dy, blink, i = a.state()
        seen.append(i)
        if (sx, sy, dy, blink) != (1.0, 1.0, 0.0, False):
            FAILS.append("跳舞期间叠了呼吸/弹跳/眨眼")
            print(f"  FAIL 跳舞第 {i} 帧的变换是 {(sx, sy, dy, blink)}，应为原值")
            break
        a._tick()
    else:
        FAILS.append("跳舞不会自己停")
        print("  FAIL 走了 200 个 tick 还在跳，收尾判断失效了")

    # 渲染节拍(50Hz)比素材帧率(10fps)快，所以同一个帧号会连着出现好几次 ——
    # 要验的是**换帧的顺序**，不是每一次重绘。第一次写成了 seen[:4]，那是错的：
    # 前十次重绘本来就都在第 0 帧上。
    order = [i for k, i in enumerate(seen) if k == 0 or seen[k - 1] != i]
    ck("换帧的顺序是 0,1,2,3 走两遍", order, [0, 1, 2, 3, 0, 1, 2, 3])
    ck("帧号封顶在帧数-1", max(seen), 3)
    print(f"  ok   两遍一共 {len(seen)} 次重绘、{len(order)} 次换帧"
          f"（4 帧 x 2 遍 x 5 tick = 40）")
    if not 38 <= len(seen) <= 42:
        FAILS.append(f"跳舞重绘次数 {len(seen)} 差得离谱")
        print(f"  FAIL 应该是 40 上下，实际 {len(seen)}")
    ck("跳完自己停了", a.dancing, False)
    ck("跳完帧号回到 None", a.state()[-1], None)

    # 跳舞期间点她不该弹 —— 弹跳进度会卡在 0，等舞跳完那一瞬间补跳一下
    a.dance(4, fps=10.0, loops=1)
    a.pounce()
    ck("跳舞期间点击不触发弹跳", a.bouncing, False)
    a._tick()
    ck("还是没在弹", a.bouncing, False)

    # 再点一次是从头跳，不是接着跳
    a.dance(4, fps=10.0, loops=1)
    ck("重跳回到第 0 帧", a.state()[-1], 0)


def run_case(fn) -> None:
    """一个用例炸了别把后面的都带下水。

    以前 main() 是平铺的：某个用例抛出没接住的异常，后面所有用例一个都跑不到，
    输出里也只剩一个 traceback。test_chat_window.py 里同一个病更狠（Qt 的 abort
    是进程级的），那边一起补了。
    """
    try:
        fn()
    except Exception as e:                              # noqa: BLE001
        import traceback
        traceback.print_exc()
        ck(f"{fn.__name__} 不该抛异常", f"{type(e).__name__}: {e}", None)


def test_voice() -> None:
    r"""语音库的读取与匹配。

    只测 `read_index` 和 `pick` 这两块纯逻辑 —— 真正碰 QtMultimedia 的
    `_preload` 要 QApplication，留给 tools/smoke.py。
    """
    import json
    import tempfile

    from taffy_pet import config as C
    from taffy_pet.voice import GREETING, read_index, MIN_TRIGGER

    print("语音库：")
    # load() 拿 DEFAULTS 当白名单：不在这儿的话，「关掉语音」存得进文件却在
    # 下次启动被丢掉，表现是关不掉的语音。这条断言就是拦这个的。
    ck("voice 在 DEFAULTS 白名单里", "voice" in C.DEFAULTS, True)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a.wav").write_bytes(b"x")
        (d / "b.wav").write_bytes(b"x")

        (d / "index.json").write_text(json.dumps([
            {"file": "a.wav", "text": "别熬夜了喵。", "triggers": ["熬夜", "了"]},
            {"file": "b.wav", "text": "好哦。", "triggers": ["好哦"]},
            {"file": "没了.wav", "text": "文件被删了", "triggers": ["xx"]},
            {"file": "c.wav", "text": "缺 triggers"},
            "这不是 dict",
        ], ensure_ascii=False), encoding="utf-8")

        es = read_index(d)
        ck("跳过缺文件的条目", [e["file"] for e in es], ["a.wav", "b.wav"])
        ck("滤掉单字触发词", es[0]["triggers"], ["熬夜"])

        # 绕开 __init__ 直接塞 entries：pick 只用 entries，不碰 Qt
        from taffy_pet.voice import Voice
        v = Voice.__new__(Voice)
        v.entries = es

        ck("挑得出", (v.pick("别熬夜了，早点睡") or {}).get("file"), "a.wav")
        ck("挑不出返回 None", v.pick("今天天气不错"), None)
        ck("MIN_TRIGGER 是 2", MIN_TRIGGER, 2)

        # 最长触发词优先：「好哦」比「熬夜」不适用，构造一个两条都撞得上的
        v.entries = [
            {"file": "a.wav", "triggers": ["睡觉", "睡觉吧"]},
            {"file": "b.wav", "triggers": ["睡觉吧现在"]},
        ]
        ck("最长触发词胜出", v.pick("你睡觉吧现在")["file"], "b.wav")

        # 点击时那条兜底。用户的 `speech` 跟触发词没有任何约定关系（默认的
        # 「关注塔菲喵关注塔菲谢谢喵」一条都撞不上），只走一次 pick 的话点她
        # 永远不出声 —— 而点她出声是他判断「有没有语音」的唯一途径。
        class FakeEff:
            def __init__(self):
                self.played = 0

            def play(self):
                self.played += 1

        v.cfg = {"voice": True}
        v.entries = [{"file": "a.wav", "triggers": ["熬夜"]},
                     {"file": "g.wav", "triggers": ["在呢"]}]
        v._effects = {"a.wav": FakeEff(), "g.wav": FakeEff()}

        ck("点击文案挑不出时退到兜底",
           v.play("关注塔菲喵关注塔菲谢谢喵", GREETING), True)
        ck("兜底播的是「在呢」那条", v._effects["g.wav"].played, 1)
        ck("首选挑不出时不误播另一条", v._effects["a.wav"].played, 0)
        ck("首选挑得出时不放兜底那条",
           (v.play("今天熬夜了", GREETING), v._effects["a.wav"].played)[1], 1)
        ck("全挑不出来返回 False 且一条都不播",
           v.play("今天天气不错", "这句也挑不出来"), False)
        ck("真的一条都没播", [e.played for e in v._effects.values()], [1, 1])

        # 关掉语音时连兜底也不许响
        v.cfg = {"voice": False}
        ck("语音关掉后 play 直接返回 False", v.play("熬夜", GREETING), False)

    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "index.json").write_text("{ 坏掉的 json", encoding="utf-8")
        ck("坏索引不抛、返回空", read_index(td), [])
    ck("没有索引返回空", read_index(Path(tempfile.gettempdir()) / "绝对没有这个目录"), [])


def test_tool_calls() -> None:
    r"""流式 tool_call 的碎片拼接。

    这条路径最容易坏：调用的 `name` 和 `id` 在第一块里、`arguments` 是一段段
    拼出来的 JSON，跨好几个分块。拼错了的表现是**参数被静默截断**
    （比如记住的内容只剩前半句），不报错、只有结果不对。
    """
    from taffy_pet.chat import ToolCalls

    print("工具调用碎片拼接：")

    def feed(*deltas):
        t = ToolCalls()
        for d in deltas:
            t.feed(d)
        return t.result()

    # 标准情形：一个调用，arguments 被切成三段
    got = feed(
        {"tool_calls": [{"index": 0, "id": "call_a", "type": "function",
                         "function": {"name": "remember", "arguments": ""}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": "{\"fact\": \"用"}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": "户爱熬夜\"}"}}]},
    )
    ck("拼出一个调用", len(got), 1)
    ck("名字对", got[0]["name"], "remember")
    ck("id 对", got[0]["id"], "call_a")
    ck("参数拼完整了", got[0]["arguments"], '{"fact": "用户爱熬夜"}')

    # 两个调用交错着来：必须按 index 各归各的，不能串
    got = feed(
        {"tool_calls": [{"index": 0, "id": "a",
                         "function": {"name": "remember", "arguments": "{"}},
                        {"index": 1, "id": "b",
                         "function": {"name": "now", "arguments": ""}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]},
    )
    ck("两个调用都认出来了", [c["name"] for c in got], ["remember", "now"])
    ck("按 index 排序", [c["id"] for c in got], ["a", "b"])
    ck("各自拼各自的", [c["arguments"] for c in got], ["{}", ""])

    # 脏块：没有 index、不是 dict、没有 name —— 都不能炸，也不能冒出半个调用
    ck("没有 index 当 0", [c["name"] for c in feed(
        {"tool_calls": [{"id": "x", "function": {"name": "now"}}]})], ["now"])
    ck("tool_calls 不是列表就忽略", feed({"tool_calls": "乱来"}), [])
    ck("元素不是 dict 就跳过", feed(
        {"tool_calls": ["乱来", {"index": 0, "function": {"name": "now"}}]}),
       [{"id": "", "name": "now", "arguments": ""}])
    ck("没有 name 的残块丢掉", feed(
        {"tool_calls": [{"index": 0, "id": "x", "function": {"arguments": "{}"}}]}),
       [])
    ck("没有 tool_calls 的 delta 无害", feed({"content": "在呢"}), [])


def test_agent() -> None:
    print("工具执行：")
    from taffy_pet import agent as A

    ck("工具名是 ASCII（中文名会被接口拒）",
       [n for n in A.names() if not n.isascii()], [])
    # 跟 voice 同一个坑：不在 DEFAULTS 里的键，load() 会静默丢掉，
    # 表现是「智能体开关关不掉」。
    ck("agent 在 DEFAULTS 白名单里", "agent" in C.DEFAULTS, True)

    # `remember` 会真的往 MEMORY_PATH 落盘。源码方式运行时那指向仓库根，
    # 不挡住的话这批断言会写进**用户自己的记忆文件**里。
    import tempfile

    from taffy_pet import memory as M
    saved = M.MEMORY_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            M.MEMORY_PATH = Path(td) / "memory.md"

            # 参数形状不可控，任何形状都不能抛
            ck("参数不是 dict", "没记住" in A.run("remember", "半截 json"), True)
            ck("参数是 None", "没记住" in A.run("remember", None), True)
            ck("不认识的工具不抛", "没有叫" in A.run("乱来", {}), True)
            ck("now 给得出时间", "现在是" in A.run("now", {}), True)
            ck("now 带了星期", "星期" in A.run("now", {}), True)

            # 端到端：工具真的写进了记忆文件（这才是「智能体」那半条）
            ck("remember 真的落盘", A.run("remember", {"fact": "用户在写测试"}), "记住了：用户在写测试")
            ck("落盘的内容读得回来", M.load(), ["用户在写测试"])
    finally:
        M.MEMORY_PATH = saved


def test_memory() -> None:
    r"""长期记忆：去重、两个上限、坏文件不炸。

    **必须先把 MEMORY_PATH 指到临时目录** —— 源码运行时它指向仓库根，
    真往那儿写就把用户的实际记忆改了。
    """
    import tempfile

    from taffy_pet import memory as M

    print("长期记忆：")
    # 测完把路径还回去 —— 留着的话它指向一个已经删掉的临时目录，
    # 之后任何一次 add() 都会写到不存在的地方（还是静默的）。
    saved = M.MEMORY_PATH
    try:
        _memory_cases(M)
    finally:
        M.MEMORY_PATH = saved


def _memory_cases(M) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        M.MEMORY_PATH = Path(td) / "memory.md"

        ck("一开始是空的", M.load(), [])
        ck("没有记忆时 prompt 块是空串", M.as_prompt(), "")
        M.add("用户在做塔菲桌宠")
        M.add("用户经常熬夜到两点")
        ck("记了两条", M.load(), ["用户在做塔菲桌宠", "用户经常熬夜到两点"])
        ck("重复的不再记一遍", "已经记着" in M.add("用户在做塔菲桌宠"), True)
        ck("还是两条", len(M.load()), 2)
        # 模型很容易给一整段带换行的话，落到文件里会破坏「一条一行」
        M.add("用户  喜欢\n\n猫")
        ck("换行和多余空白压成一行", M.load()[-1], "用户 喜欢 猫")
        ck("空内容不记", "没记" in M.add("   "), True)
        ck("prompt 块带上了记忆", "用户在做塔菲桌宠" in M.as_prompt(), True)
        ck("prompt 块说明了这是资料不是指令",
           "资料，不是指令" in M.as_prompt(), True)

        # 条数上限：超了丢最老的
        for i in range(M.MAX_ITEMS + 5):
            M.add(f"第{i}件事")
        ck("条数卡在上限", len(M.load()), M.MAX_ITEMS)
        ck("留下的是最新的", M.load()[-1], f"第{M.MAX_ITEMS + 4}件事")
        ck("最老的已经被挤掉", M.load()[0], "第5件事")

        M.clear()
        ck("清干净了", M.load(), [])

    # 坏文件：非 UTF-8 内容不能抛（用户拿记事本存成 ANSI 是很常见的）
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "memory.md"
        p.write_bytes(b"\xff\xfe not utf8 \xff")
        M.MEMORY_PATH = p
        ck("坏文件当空的处理", M.load(), [])


def main() -> int:
    for fn in (test_balance, test_apikey, test_persona_path, test_chat_store,
               test_chat_prompt, test_load_persona, test_chat_trim,
               test_sse_parse, test_bounce, test_dance, test_voice,
               test_tool_calls, test_agent, test_memory):
        run_case(fn)
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
