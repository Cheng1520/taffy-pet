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

            # 坏文件不能让程序起不来
            CS.CHAT_PATH.write_text("{不是合法 json", encoding="utf-8")
            ck("坏文件当空的处理", CS.load(), [])
            CS.CHAT_PATH.write_text('{"version": 999, "messages": []}', encoding="utf-8")
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
    from pathlib import Path
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


def main() -> int:
    test_balance()
    test_apikey()
    test_persona_path()
    test_chat_store()
    test_chat_prompt()
    test_load_persona()
    test_chat_trim()
    test_sse_parse()
    test_bounce()
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
