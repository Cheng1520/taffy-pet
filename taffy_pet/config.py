"""配置读写。

API Key 的优先级：环境变量 DEEPSEEK_API_KEY > config.json。
环境变量优先是为了让人可以临时换 Key 而不动文件；
放到 config.json 则是给不想配环境变量的人用的（右键菜单里能填）。

config.json 在 .gitignore 里 —— 里面有 Key，绝不能进版本库。
"""
import json
import os

from .paths import CONFIG_PATH, ensure_data_dir

ENV_KEY = "DEEPSEEK_API_KEY"

DEFAULTS = {
    "api_key": "",
    # 角色在屏幕上的高度，单位是「逻辑像素」：和 Qt 的窗口坐标同一套单位，
    # 会跟着 Windows 的缩放比例走。200% 缩放的屏上，200 逻辑像素 = 400 物理像素。
    # 想让她更大/更小就改这个数。
    "height": 200,
    "opacity": 1.0,
    "always_on_top": True,
    "blink": True,
    "speech": "关注塔菲喵关注塔菲谢谢喵",
    "hint_shown": False,    # 首次运行提示过操作方式了吗
    "volume": 1.0,
    "pos": None,            # [x, y]，上次退出时的位置
    # 聊天窗口上次的位置和大小。写它的是 chat_window，读它的是 ChatWindow._restore_geometry；
    # 但**读得回来**这件事靠的是这儿有这一项 —— load() 拿 DEFAULTS 当白名单，不在这儿
    # 的键存得进文件、读的时候被静默丢掉，表现是「窗口大小永远记不住」。
    "chat_geometry": None,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (OSError, json.JSONDecodeError) as e:
            print(f"[config] 读不了 config.json（{e}），改用默认值")
    return cfg


def save(cfg: dict) -> None:
    """原子写：先写 .tmp 再 os.replace，跟 chat_store.save 一个形状。

    以前是直接覆盖目标文件。那份内容里有 API Key，正好在写到一半时被杀掉/断电，
    文件就废了 —— 用户看到的是「API Key 每次启动都变空」，而盘里那个坏文件一个字
    线索都不给。多写一个 .tmp 基本不要钱（这份配置写得很少）。

    代价是进程死在中间会在数据目录里留下一个 config.json.tmp，里面同样是整份配置
    （含 Key），所以它跟 config.json 一样必须在 .gitignore 里 —— 不然 `git add .`
    会把它卷进版本库。
    """
    ensure_data_dir()      # 打包之后数据目录在 %APPDATA%，第一次可能是空的
    tmp = CONFIG_PATH.parent / (CONFIG_PATH.name + ".tmp")
    try:
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
    except OSError as e:
        print(f"[config] 存不了 config.json：{e}")


def api_key(cfg: dict) -> str:
    return (os.environ.get(ENV_KEY) or cfg.get("api_key") or "").strip()


def api_key_source(cfg: dict) -> str:
    if os.environ.get(ENV_KEY):
        return f"环境变量 {ENV_KEY}"
    if (cfg.get("api_key") or "").strip():
        return "config.json"
    return "未设置"
