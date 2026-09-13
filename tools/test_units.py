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
    test_bounce()
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
