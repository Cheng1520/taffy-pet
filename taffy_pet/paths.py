"""东西都在哪 —— 源码运行和打包成 exe 之后差别很大，全收在这里。

别的模块不要再自己写 `Path(__file__)`，打包之后那个路径是错的。

|          | 源码运行       | 打包成 exe 之后                    |
| -------- | -------------- | ---------------------------------- |
| 素材     | 项目里的 assets/ | PyInstaller 解压出来的临时目录     |
| 数据     | 项目根目录     | `%APPDATA%\\TaffyPet`               |

数据（config.json / taffy.log）必须和程序分开放：装到 `Program Files` 之后那个
目录是只读的，普通权限写不进去，config.json 存那儿会静默失败 —— 表现就是
「每次启动 API Key 都要重填」。

临时目录每次启动都可能不一样，而且退出就没了，所以只有只读的素材能放那儿。
"""
import os
import sys
from pathlib import Path

APP_NAME = "TaffyPet"

# PyInstaller 冻结之后会设 sys.frozen，并把解压目录放在 sys._MEIPASS
FROZEN = bool(getattr(sys, "frozen", False))
_BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent

# 素材：只读，跟着程序走
ASSETS = (_BUNDLE / "assets") if FROZEN else (ROOT / "assets")

# 数据：可写，放用户目录
DATA_DIR = (
    Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    if FROZEN else ROOT
)
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "taffy.log"

# 人设：用户目录里的优先，没有就用随包的默认版
PERSONA_PATH = DATA_DIR / "persona.md"
PERSONA_DEFAULT = ASSETS / "persona.md"

# 聊天记录。里面是用户的全部对话，跟 config.json 一样绝不能进版本库
CHAT_PATH = DATA_DIR / "chat.json"


def persona_path() -> Path:
    """用户改过就用用户的，否则用随包的默认版。

    每次都重新判断，不缓存 —— 用户点完「编辑人设」保存，下一条消息就该用上，
    不该等到重启。这个函数每轮对话只调一次，开销可以忽略。
    """
    return PERSONA_PATH if PERSONA_PATH.exists() else PERSONA_DEFAULT


def ensure_data_dir() -> None:
    """第一次运行（或装完第一次启动）时把用户目录建出来。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[paths] 建不了数据目录 {DATA_DIR}：{e}")
