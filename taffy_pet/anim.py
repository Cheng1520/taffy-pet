"""动画：呼吸、点击弹跳、眨眼，以及跳舞。

呼吸、弹跳、眨眼三样只有一张立绘，全靠变换做出来，不依赖任何额外素材。
跳舞是唯一的例外：它真的需要额外素材（一张多帧的精灵表），没有就不跳。

呼吸和弹跳都作用在「底部中心」这个锚点上 —— 绕中心缩放会让脚离地，
看上去像飘着；锚在底部才像站着在起伏。

弹跳这里用关键帧而不是弹簧。先试过弹簧（欠阻尼，点击给一个速度冲量），
但阻尼会把回弹那一侧吃掉：压扁 5%，拉伸只剩 1%，看着就是「沉了一下」，
不像跳起来。关键帧能把压扁/腾空/落地分开写，每一步的幅度都能算准、能验证。

### 跳舞为什么是「覆盖」而不是「叠加」

跳的时候呼吸和弹跳**一律不参与**，state() 直接返回原始缩放。原因有两层：

1. 那些帧本身就是完整的动作，再叠一层呼吸的起伏就是抖；
2. 更实际的是 —— 帧是**按人物底部对齐**画的，而呼吸/弹跳会改纵向缩放和位移，
   叠上去之后每帧的脚底位置都在变，看着像在地上滑。

眨眼的道理一样：那些帧里她的表情是自己画好的，硬给她换一张闭眼立绘更怪。
"""
import math
import random

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

FPS_MS = 20              # 50fps，够顺，也不至于让半透明窗口一直重绘到卡

BREATH_PERIOD = 3.4      # 呼吸周期（秒）
BREATH_AMP = 0.008       # 纵向起伏幅度
BREATH_WIDE = 0.45       # 横向反向幅度（大致保持体积不变）

BOUNCE_MS = 620          # 一次点击弹跳的总时长
HOP = 0.016              # 腾空高度，占角色高度的比例

# 这里所有量都是比例，没有写死的像素 —— 角色显示大小由 config 的 height 决定，
# 换个尺寸就得让动画跟着等比缩放。之前 HOP 是写死的 12px，角色从 760px 缩到
# 200px 之后这一跳相对高度翻了三倍，呆毛直接被窗口顶边裁掉。

# (进度, 纵向缩放, 横向缩放, 离地比例)
# 先压扁 -> 蹬地窜起并拉长 -> 滞空 -> 落地压回 -> 复位
#
# 拉伸这一侧的峰值刻意压在 1.03：拉伸会让角色向上长高，长多少就要在窗口里
# 预留多少透明边距，而边距越大，窗口挡住周围桌面图标的面就越大。
# 压扁到 0.93 才是主视觉，而它是变小，不占边距。
BOUNCE_KEYS = [
    (0.00, 1.000, 1.000, 0.00),
    (0.13, 0.930, 1.045, 0.00),
    (0.30, 1.030, 0.980, 1.00),
    (0.48, 1.026, 0.983, 1.00),
    (0.74, 0.982, 1.010, 0.03),
    (1.00, 1.000, 1.000, 0.00),
]

BLINK_MS = 130
BLINK_MIN_S = 2.8
BLINK_MAX_S = 6.0

# 朝鼠标倾斜。她只有一张平面立绘，没有骨骼也没有分层，所以「转头看你」只能用
# **整体绕脚底转一个很小的角度**来装作。幅度一大立刻露馅 —— 整张图在转，裙子
# 和头发会跟着歪；3.2 度是「看得出她在动」和「看得出是假转」之间的那条线。
#
# 绕的是**脚底**（跟呼吸、弹跳同一个锚点）。绕腰或绕中心的话脚会离开地面，
# 看着像飘在半空转。
GAZE_MAX_DEG = 3.2
GAZE_MAX_SHIFT = 0.012    # 朝鼠标那一侧平移的比例（占角色宽度），垫一点视差
GAZE_TAU = 0.30           # 平滑时间常数（秒）。不平滑的话她像被一根线拽着走

DANCE_LOOPS = 3          # 点一次「跳个舞」跳几遍
DANCE_FPS = 15.0         # 兜底帧率。正常走 dance.json 里的那个

# 闲置够久之后她自己挑一件事做。
#
# 这个功能存在的理由只有一个：**没人会对桌宠右键**。跳舞、说话这些动作
# 藏在右键菜单里等于没有 —— 得她自己演出来，才有人看得见。
IDLE_MIN_S = 45.0
IDLE_MAX_S = 110.0
# (动作, 权重)。权重大致按「打扰程度」的倒数给：说话最轻，蹦一下次之，
# 跳舞最占地方、最抢戏，所以最少出现。
IDLE_ACTIONS = (("say", 5), ("hop", 3), ("dance", 2))


def _sample(u: float):
    """在关键帧之间插值，用 smoothstep 过渡，否则关键帧处会有折角。"""
    if u <= BOUNCE_KEYS[0][0]:
        return BOUNCE_KEYS[0][1:]
    for (u0, *v0), (u1, *v1) in zip(BOUNCE_KEYS, BOUNCE_KEYS[1:]):
        if u0 <= u <= u1:
            t = (u - u0) / (u1 - u0)
            t = t * t * (3.0 - 2.0 * t)
            return [a + (b - a) * t for a, b in zip(v0, v1)]
    return BOUNCE_KEYS[-1][1:]


def bounce_curve(steps: int = 400):
    """把一次弹跳完整采样出来，返回 (sx, sy, dy) 列表。

    纯函数，不碰 QObject —— 测试里要能直接调，不然就得先起一个 QApplication。
    """
    out = []
    for i in range(steps + 1):
        sy, sx, lift = _sample(i / steps)
        out.append((sx, sy, -lift * HOP))
    return out


def idle_delay(roll: float) -> float:
    """两次「她自己找事做」之间隔多久（秒）。`roll` 取 [0, 1)。

    纯函数：测试直接喂数，不用真等 45 秒。
    """
    return IDLE_MIN_S + (IDLE_MAX_S - IDLE_MIN_S) * max(0.0, min(1.0, roll))


def pick_idle_action(roll: float) -> str:
    """闲置时她做什么，返回 IDLE_ACTIONS 里的动作名。`roll` 取 [0, 1)。

    纯函数，理由同 `bounce_curve`：不然要验「三个动作都分得到」就得起
    QApplication、还得等一个 45 秒的定时器。
    """
    acc = 0.0
    total = float(sum(w for _, w in IDLE_ACTIONS))
    for name, w in IDLE_ACTIONS:
        acc += w / total
        if roll < acc:
            return name
    # 浮点累加偶尔到不了 1.0，兜到最后一项而不是抛异常
    return IDLE_ACTIONS[-1][0]


class PetAnimator(QObject):
    """每帧发一次 frame，窗口收到就重绘。"""

    frame = pyqtSignal()

    def __init__(self, blink_enabled: bool = False, parent=None):
        super().__init__(parent)
        self.blink_enabled = blink_enabled
        self._t = 0.0
        self._bounce = 1.0        # 弹跳进度，1.0 = 已结束
        self._blinking = False

        # 朝鼠标的方向，-1 = 她在屏幕最左边看着的鼠标、+1 = 最右边。
        # 分「目标」和「当前」两个：目标每帧由窗口按鼠标位置算出来，当前值
        # 在 _tick 里朝它追 —— 直接拿目标画的话，鼠标一动她就瞬移一下。
        self._gaze_target = 0.0
        self._gaze = 0.0

        # 跳舞状态。三个量分开存是因为它们的寿命不一样：
        # _dance_frames 是素材事实（取帧号要拿它取模），另外两个是这一次播放的进度。
        self._dance_frames = 0    # 0 = 没在跳
        self._dance_total = 0     # 这次一共要放多少帧（帧数 x 遍数）
        self._dance_elapsed = 0.0
        self._dance_fps = DANCE_FPS

        self._timer = QTimer(self)
        self._timer.setInterval(FPS_MS)
        self._timer.timeout.connect(self._tick)
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._start_blink)

    def start(self) -> None:
        self._timer.start()
        self._schedule_blink()

    def stop(self) -> None:
        self._timer.stop()
        self._blink_timer.stop()

    def pounce(self) -> None:
        # 跳舞的时候不接弹跳。画面上的理由是两套动作会打架，真正的原因是
        # 弹跳进度会**卡在 0**：_tick 里那一支在跳舞期间根本不走，等舞跳完
        # 才轮到它，于是她会在收尾那一瞬间莫名其妙补跳一下。
        if self.dancing:
            return
        self._bounce = 0.0

    @property
    def bouncing(self) -> bool:
        return self._bounce < 1.0

    @property
    def dancing(self) -> bool:
        return self._dance_frames > 0

    def dance(self, frames: int, fps: float = DANCE_FPS,
              loops: int = DANCE_LOOPS) -> None:
        """开始跳舞。`frames` 是精灵表里的帧数，`fps` 是原素材帧率。

        再点一次就是重头跳，不是接着跳 —— 「再跳一遍」比「跳一半被打断又接着」
        好理解。
        """
        if frames <= 0:
            return
        self._dance_frames = int(frames)
        self._dance_fps = float(fps) if fps and fps > 0 else DANCE_FPS
        self._dance_total = self._dance_frames * max(1, int(loops))
        self._dance_elapsed = 0.0
        self._bounce = 1.0
        self._blinking = False
        self._blink_timer.stop()
        self.frame.emit()

    # ---------- 每帧 ----------
    def _tick(self) -> None:
        self._t += FPS_MS / 1000.0
        # 指数趋近（不是线性）：鼠标停下来时她也要跟着停下来，线性会匀着挪半天。
        # 系数用 1-exp(-dt/tau) 而不是 dt/tau —— 后者在 dt 接近 tau 时会冲过头。
        dt = FPS_MS / 1000.0
        self._gaze += (self._gaze_target - self._gaze) * (1.0 - math.exp(-dt / GAZE_TAU))
        if self.dancing:
            self._dance_elapsed += FPS_MS / 1000.0
            if self._dance_played() >= self._dance_total:
                self._dance_frames = 0
                self._dance_total = 0
                # 跳舞期间眨眼定时器是停着的，跳完要重新排，不然从此再也不眨
                self._schedule_blink()
        elif self.bouncing:
            self._bounce = min(1.0, self._bounce + FPS_MS / BOUNCE_MS)
        self.frame.emit()

    def set_gaze(self, target: float) -> None:
        """告诉动画器鼠标在哪个方向：-1 = 她的左边，+1 = 她的右边。

        超过这个范围也没关系（屏幕比她的活动范围大得多），夹住就行 ——
        不夹的话鼠标贴到屏幕边缘时她会歪到看起来像要倒了。
        """
        self._gaze_target = max(-1.0, min(1.0, float(target)))

    @property
    def gaze_pose(self):
        """返回 (绕脚底的倾斜角度, 横向位移比例)。

        跳舞期间一律是 (0, 0)：那些帧是完整的动作，**按人物底部对齐**画好的，
        再叠一个整体旋转就是每帧的脚底位置都在变，看着像在地上打转。
        跟「跳舞为什么是覆盖而不是叠加」是同一条理由。
        """
        if self.dancing:
            return 0.0, 0.0
        return self._gaze * GAZE_MAX_DEG, self._gaze * GAZE_MAX_SHIFT

    def _dance_played(self) -> int:
        """已经放到第几帧 —— **不取模**，只用来判断跳完没有。

        别拿 frame_index() 去比 _dance_total：那个是取过模的，永远到不了总帧数，
        于是这个舞**永远跳不完**。（第一版就是这么写的，被 test_units 的
        test_dance 卡死在 while 循环里抓出来的。）
        """
        return int(self._dance_elapsed * self._dance_fps)

    def frame_index(self):
        r"""当前该画第几帧，没在跳就是 None。

        从「已经放了多久」现算，而不是每帧 +1 累加 —— 累加会让 50Hz 的渲染
        节拍（FPS_MS=20）和 15fps 的素材帧率一直错开，误差越滚越明显。
        现算的话每一帧的边界都落在 1/15 秒的整数倍上，只是被量化到最近的一次
        重绘，快慢不会漂。
        """
        if not self.dancing:
            return None
        return self._dance_played() % self._dance_frames

    # ---------- 眨眼 ----------
    def _schedule_blink(self) -> None:
        if not self.blink_enabled:
            return
        self._blink_timer.start(int(random.uniform(BLINK_MIN_S, BLINK_MAX_S) * 1000))

    def _start_blink(self) -> None:
        self._blinking = True
        self.frame.emit()
        QTimer.singleShot(BLINK_MS, self._end_blink)

    def _end_blink(self) -> None:
        self._blinking = False
        self.frame.emit()
        self._schedule_blink()

    def set_blink_enabled(self, on: bool) -> None:
        self.blink_enabled = on
        self._blink_timer.stop()
        self._blinking = False
        self._schedule_blink()
        self.frame.emit()

    # ---------- 给窗口取用 ----------
    def state(self):
        """返回 (横向缩放, 纵向缩放, 纵向位移比例, 是否闭眼, 跳舞帧号)。

        位移是「占角色高度的比例」而不是像素 —— 角色显示大小可配，像素量会跟着变。

        跳舞帧号是 None 或 0..frames-1 的整数。**不是 None 时前三个量一律是
        原值 (1, 1, 0)，闭眼一律是假** —— 见模块文档里「跳舞为什么是覆盖」。
        """
        if self.dancing:
            return 1.0, 1.0, 0.0, False, self.frame_index()

        breath = math.sin(self._t * 2 * math.pi / BREATH_PERIOD)
        sy = sx = 1.0
        dy = 0.0
        if self.bouncing:
            sy, sx, lift = _sample(self._bounce)
            # **一定要转成 -lift*HOP**，跟 `bounce_curve` 用同一个换算。
            #
            # 这里原来直接把 `_sample` 的第三个值当 dy 传出去 —— 那是「离地比例」
            # （0~1），不是一个占角色高度的位移量。调用方 pet.py 拿它去乘
            # `disp_h`，于是每点她一次，她就**向下挪一整只角色的高度**：200px 的
            # 角色整个掉出 226px 的窗口，只剩头顶露在底下，再慢慢滑回来。
            #
            # 之所以一直没被发现：冒烟测试的边界检查走的是 `bounce_curve()`
            # （那个函数换算是**对的**），而窗口走的是 state()。两边算的是同一件
            # 事、却是两份实现，测试量的跟屏幕上画的不是一条路。
            # `test_bounce_state` 现在把两者钉在一起。
            dy = -lift * HOP
        sy += BREATH_AMP * breath
        sx -= BREATH_AMP * BREATH_WIDE * breath
        return sx, sy, dy, self._blinking, None
