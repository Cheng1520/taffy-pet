r"""让她**出声** —— 从预渲染的语音库里挑一条最贴的播出来。

### 为什么不是「把回复实时念出来」

合成用的是 F5-TTS，跑在这台机器（CPU，无独显）上 RTF 28~57，也就是
**一句两秒半的话要算 70~140 秒**。实时合成在这台机器上不存在任何折中方案。

所以这里做的是另一件事：台词**事先渲染好**，回复进来时按关键词挑一条最像的播。
挑不到就**不出声** —— 随便播一条不相关的话比安静更像 bug。

### 素材放哪

语音库是**克隆真人（永雏塔菲）声音**的产物，只做本机自用。
它住在用户数据目录 `%APPDATA%\TaffyPet\voice\`（见 `paths.VOICE_DIR`），
**不进版本库，但会打进安装包** —— 要的是「GitHub 上发出去的安装包装完和本机一致」。
仓库里只有生成它的管线（`taffy_lib.py`）和这份读取逻辑，音频本身被 `/voice/` 忽略掉，
再由 `installer\taffy-pet.iss` 收进安装程序。README 顶上有对应的免责声明。

语音库的结构：

    voice/
      index.json    [{file, text, triggers: [...], ...}, ...]
      01.wav 02.wav ...

`index.json` 缺失或读坏了都只是**没有语音**，不影响其他任何功能。
"""
import json
from pathlib import Path

from . import paths

# 短于这个长度的触发词一律忽略。
#
# 单字触发词（「嗯」「累」「玩」）在任意回复里撞上的概率太高了，撞上就播一句
# 对不上的话，听感上是「这货在乱说」。宁可少播。
MIN_TRIGGER = 2


def read_index(d) -> list:
    r"""读 `d/index.json`，返回能用的条目。

    **和 Qt 分开**：这个是纯函数，`tools/test_units.py` 直接拿临时目录测它，
    不用起 QApplication。`Voice` 才是碰 QtMultimedia 的那一半。

    「能用的」= 结构对（dict、有 file、triggers 是列表）+ 文件真在。
    索引里有、wav 被删了 → 跳过这一条而不是整份作废，用户补删文件时不会全哑。
    """
    src = Path(d) / "index.json"
    if not src.exists():
        return []
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[voice] 读不了语音库索引 {src}：{e}")
        return []

    out = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        f, trig = e.get("file"), e.get("triggers")
        if not f or not isinstance(trig, list):
            continue
        if not (Path(d) / f).exists():
            continue
        e["triggers"] = [t for t in trig
                         if isinstance(t, str) and len(t) >= MIN_TRIGGER]
        out.append(e)
    return out


class Voice:
    """语音库 + 播放。

    寿命跟着 Pet 走。QtMultimedia 缺失、库为空、文件损坏 —— 任何一种情况都
    只是 `available` 为假，**不抛异常**：语音是锦上添花，不能因为它挂掉就
    让桌宠起不来。
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.entries: list = []
        self._effects: dict = {}
        self._dir = paths.VOICE_DIR
        self.entries = read_index(self._dir)
        if self.entries:
            self._preload()

    def _preload(self) -> None:
        r"""把每条都做成一个 QSoundEffect 备着。

        **不能等到播的时候再 setSource** —— QSoundEffect 是异步解码的，
        setSource 之后立刻 play，音频还没解开，play 会静默什么都不做。
        开机一次性建好，之后 play 才是即时的。
        """
        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtMultimedia import QSoundEffect
        except ImportError as e:
            print(f"[voice] 没有 QtMultimedia，语音关掉：{e}")
            return

        vol = float(self.cfg.get("volume", 1.0))
        for e in self.entries:
            eff = QSoundEffect()
            eff.setSource(QUrl.fromLocalFile(str(self._dir / e["file"])))
            eff.setVolume(vol)
            self._effects[e["file"]] = eff

    # ---------- 对外 ----------
    @property
    def available(self) -> bool:
        return bool(self._effects) and bool(self.cfg.get("voice", True))

    def pick(self, text: str):
        r"""挑一条最贴的，挑不到返回 None。

        **命中最长的触发词优先**（并列时取索引靠前的）。
        长的更具体：「别熬夜了」比「了」有信息量得多。
        """
        best, score = None, 0
        for e in self.entries:
            for t in e["triggers"]:
                if t in text and len(t) > score:
                    best, score = e, len(t)
        return best

    def play(self, text: str) -> bool:
        r"""播一条跟 `text` 最贴的。播了返回 True。"""
        if not self.available:
            return False
        e = self.pick(text)
        if e is None:
            return False
        eff = self._effects.get(e["file"])
        if eff is None:
            return False
        eff.play()
        return True
