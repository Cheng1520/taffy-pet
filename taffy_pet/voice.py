r"""让她**出声** —— 从预渲染的语音库里挑一条最贴的播出来。

### 为什么不是「把回复实时念出来」

合成用的是 F5-TTS，跑在这台机器（CPU，无独显）上 RTF 28~57，也就是
**一句两秒半的话要算 70~140 秒**。实时合成在这台机器上不存在任何折中方案。

所以这里做的是另一件事：台词**事先剪好**，回复进来时按关键词挑一条最像的播。
挑不到就**不出声** —— 随便播一条不相关的话比安静更像 bug。

### 库里是她的**原声**，不是克隆的

早期版本用 F5-TTS / XTTS 克隆，出来的东西用户听了一句「雷霆声音」就否了。
那不是调参问题：拿说话人验证（cam++）量，她真实录音的留一法区间是
**0.602 ~ 0.853**，而**每一个**克隆输出都落在 0.405 ~ 0.545 —— 整批低于
她自己录音的下限。克隆这条路在这台机器上没有出路，于是改成只用原声。

现在的库是**从公开视频里按词级时间戳剪出来的真人片段**，一句一文件。
来源是粉丝整理的语音包和「怪话怪叫」合集；直播录播本身没用 —— 那些是情境对话，
剪出来就是「汪小母狗小母狗」这类，做不了台词。

挑句子的标准、以及「像不像她」怎么量，在 `_work/` 下那几个构建期脚本里
（`words.py` 按停顿切短语、`calib.py` 按片段长度标定门槛、`audit.py` 转写+打分）。
**那些不随包发布**：`_work/` 被忽略，仓库里只有这份读取逻辑。

### 素材放哪

它住在用户数据目录 `%APPDATA%\TaffyPet\voice\`（见 `paths.VOICE_DIR`），
**不进版本库，但会打进安装包** —— 要的是「GitHub 上发出去的安装包装完和本机一致」。
音频本身被 `/voice/` 忽略掉，再由 `installer\taffy-pet.iss` 收进安装程序。
README 顶上有对应的免责声明。

语音库的结构：

    voice/
      index.json    [{file, text, triggers: [...], ...}, ...]
      01.wav 02.wav ...

`index.json` 缺失或读坏了都只是**没有语音**，不影响其他任何功能。
"""
import json
import random
from pathlib import Path

from . import paths

# 短于这个长度的触发词一律忽略。
#
# 单字触发词（「嗯」「累」「玩」）在任意回复里撞上的概率太高了，撞上就播一句
# 对不上的话，听感上是「这货在乱说」。宁可少播。
MIN_TRIGGER = 2

# 点击她时的兜底招呼，见 `Voice.play` 的文档。
#
# `speech`（默认「关注塔菲喵关注塔菲谢谢喵」）是用户自己配的文案，跟语音库的
# 触发词没有任何约定关系，对不上才是常态。所以点击时拿它当**首选**，
# 挑不出来再退到这句 —— 而点她**必须出声**，那是用户判断「这玩意儿到底有没有
# 语音」的唯一途径。
#
# 「在呢」挂在 04.wav（一声喵）的触发词上。触发词是**匹配用的，不是转写**：
# 被打招呼就喵一声，本来就是猫的正确反应。那一条的 `text` 仍是真实转写「喵喵喵」。
# 换语音库时记得核这条 —— 新库里没有任何触发词命中「在呢」的话，点她就哑了。
GREETING = "在呢在呢"


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


def pick_entry(entries: list, text: str):
    r"""从 `entries` 里挑一条最贴 `text` 的，挑不到返回 None。

    **和 Qt 分开**，跟 `read_index` 一样是纯函数 —— `tools\demo.py` 拿它算
    「真程序在这一句话上会播哪一条」。演示里播的必须是真程序会播的那一段，
    写死文件名的话库里一换台词，视频就开始演示一个不存在的行为。

    **命中最长的触发词优先**（并列时取索引靠前的）。
    长的更具体：「别熬夜了」比「了」有信息量得多。
    """
    best, score = None, 0
    for e in entries:
        for t in e["triggers"]:
            if t in text and len(t) > score:
                best, score = e, len(t)
    return best


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
        r"""挑一条最贴的，挑不到返回 None。判据见 `pick_entry`。"""
        return pick_entry(self.entries, text)

    def play_entry(self, entry: dict) -> bool:
        r"""直接播某一条，不做关键词匹配。播了返回 True。

        `play` 是「给我一句话，你自己找最贴的」，这个是「就这条」——
        条目已经挑好了的地方（她自己找事做时抽到的那句）走这条，
        否则还得让 `pick` 在同样的文本上再猜一次，猜不出就白抽了。
        """
        if not self.available or not entry:
            return False
        eff = self._effects.get(entry.get("file"))
        if eff is None:
            return False
        eff.play()
        return True

    def play(self, text: str, *fallbacks: str) -> bool:
        r"""播一条跟 `text` 最贴的。播了返回 True，一条都挑不出来返回 False。

        `fallbacks` 依次兜底，理由在 `GREETING` 那儿：点击那一下的文案跟语音库
        没有任何约定关系，只走一次 `pick` 的话，用户点她永远没声 —— 而点她出声
        是他判断「这玩意儿到底有没有语音」的唯一途径。
        """
        if not self.available:
            return False
        for t in (text, *fallbacks):
            e = self.pick(t)
            if e is not None and self.play_entry(e):
                return True
        return False

    def random_line(self):
        r"""随便挑一条台词，挑不到返回 None。她自己找事做的时候用。

        **不做匹配**：这是「她自己想说点什么」，不是「回应用户说了什么」，
        所以随便哪条都成立，挑得越散越好 —— 老是同一句会立刻被听出来。
        """
        if not self.available:
            return None
        return random.choice(self.entries)
