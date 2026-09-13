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
    ensure_data_dir()      # 打包之后数据目录在 %APPDATA%，第一次可能是空的
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
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
