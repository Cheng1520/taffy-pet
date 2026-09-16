"""聊天窗的暖色配色。

塔菲是粉头发 + 暖棕马甲 + 红蝴蝶结，聊天窗就该是她的颜色。以前这儿没有配色表，
气泡那几个常量散在 chat_window.py 顶上、QSS 里又是一堆十六进制字面量 ——「以后改
配色」得在两个文件三种写法里找。

**底色仍然只有一个来源**：画布色和描边色直接从 toast.py 拿（桌宠气泡和聊天窗必须
一起变），这个模块只在它上面加聊天窗**独有**的那几档 —— 她的气泡卡、输入区、
轨迹行那条细线。别把 toast 现有的 BG / BORDER / TEXT / DIM 复制一份过来。

颜色刻意避开了纯黑纯白：底色是暖奶油、字是深暖褐紫（TEXT），她的气泡是暖白而不是
#FFFFFF，我的气泡是玫瑰粉而不是荧光粉。整间屋子只有粉、奶油、暖棕三种色相，
没有第四种来捣乱。
"""
from PyQt5.QtGui import QColor

# 桌宠那边已有的四档，原样透出去 —— 聊天窗和气泡必须同源
from .toast import BG, BORDER, DIM, TEXT

# ---------- 消息区 ----------
# 消息区底色**不在这儿另起一档** —— 它就是 BG 本身（chat_window 的
# `#chatRoot / #chatArea` 直接用 toast.BG）。桌宠吐的那句话和聊天窗得是同一张纸。
# 气泡的描边同理，就是 BORDER（chat_window 里叫 HERS_LINE）。
CARD = QColor(255, 250, 246)           # 她的气泡：一档暖奶油粉，不用纯白

MINE = QColor(226, 139, 166)           # 我的气泡：玫瑰粉
MINE_EDGE = QColor(205, 113, 145)      # 它的描边
MINE_DEEP = QColor(190, 100, 132)      # 按下时再深一档
MINE_INK = QColor(255, 255, 255)       # 粉底上的字

# ---------- 输入区 ----------
BAR = QColor(252, 240, 235)            # 底下那条输入区的底色：淡腮红
BAR_EDGE = QColor(245, 219, 212)       # 它上面那条一分线
FIELD = QColor(255, 255, 255)          # 输入框
FIELD_EDGE = QColor(240, 208, 216)     # 输入框描边
FIELD_FOCUS = QColor(226, 139, 166)    # 聚焦时的描边

GHOST = QColor(186, 172, 177)          # 忙时按钮（它现在是「停止」）
GHOST_HOVER = QColor(170, 155, 161)

# ---------- 旁白 ----------
RULE = QColor(240, 200, 212)           # 轨迹行左边那条细线
CHIP = QColor(255, 252, 249)           # 快捷开场 / 日期胶囊的底
CHIP_EDGE = QColor(244, 214, 222)      # 它们的描边
DAY_BG = QColor(250, 236, 236)         # 日期胶囊

# ---------- 头像 ----------
AVATAR_BG = QColor(253, 240, 243)      # 立绘背后那块淡粉
AVATAR_EDGE = QColor(246, 219, 226)
