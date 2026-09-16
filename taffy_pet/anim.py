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
# 跳舞最占地方、最抢戏，所以最少出现。变身比跳舞还亮眼（整个角色的颜色
# 都会变），所以权重压到最低 —— 它是「偶尔撞见一次」的东西，不是日常。
IDLE_ACTIONS = (("say", 5), ("hop", 3), ("dance", 2), ("transform", 1))

# ---------- 变身 ----------
#
# 四拍：蓄力 -> 爆发 -> 维持 -> 消退。**没有额外素材** —— 光晕、冲击环、
# 光点、配色全都是画上去的，跟呼吸、影子同一条路子。
#
# 为什么要有「维持」这一拍、而且还这么长（5 秒）：光只有一下爆闪的话，
# 那叫「闪了一下」不叫「变身」。变身这个动作**靠状态持续**来和闪光区分开：
# 她得在光里待一会儿、身上得一直亮着、颜色得一直不一样，才读得出「她变了」。
TF_CHARGE_MS = 700      # 蓄力：光收拢、她微微下沉
TF_BURST_MS = 520       # 爆发：冲击环扫出去、白光最亮
TF_HOLD_MS = 5200       # 维持：光晕常亮、光点绕着飞、整个人泛色
TF_FADE_MS = 900        # 消退：亮度和颜色收回去
TF_MS = TF_CHARGE_MS + TF_BURST_MS + TF_HOLD_MS + TF_FADE_MS

TF_PARTICLES = 14       # 光点个数
# 变身期间她离地的高度（占角色高度的比例）。变身是「浮起来」的，脚离地
# 是这套视觉里最省事也最有效的一笔。上限受 MARGIN_RATIO 约束（见 pet.py）：
# 那个边距是照着「呼吸 + 弹跳拉伸 + 腾空」算的 6.5%，变身不走弹跳那条路，
# 所以这里只要不把余量吃光就行 —— 0.8% 加上呼吸的 0.8% 还不到 2%。
TF_HOVER = 0.008
TF_POP = 0.05           # 爆发瞬间她放大多少（比例）。同样受边距约束
# 身上那层暖金的最大不透明度。**只能这么低** —— 染色是拿金色盖掉原色，
# 越高她越像一块平的金色色斑（0.50 时五官和衣服就全糊了）。真正让「发光」
# 立起来的是轮廓光，这层只负责把她整体烘暖一点。
TF_TINT = 0.26



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


def _smooth(t: float) -> float:
    """smoothstep。关键帧和阶段之间都靠它过渡，线性会有折角。"""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _bump(u: float, peak: float = 0.22) -> float:
    """0 -> 1 -> 0 的鼓包，峰在 `peak`。两端都恰好是 0。

    阶段之间的量**必须首尾接得上**。第一版 `pop` 是「蓄力慢慢沉到 -0.02、
    爆发从 +0.05 开始」，两个阶段各自看都平滑，接缝处却是一个 7% 的瞬间跳变
    —— 在 50fps 上就是她有一条腿突然弹了一下。这类接缝自己看不出来，得把
    每个量在阶段边界上前后取一对值比一下才看得见（`test_transform` 现在会查）。
    """
    if u <= peak:
        return _smooth(u / peak) if peak > 0 else 1.0
    return 1.0 - _smooth((u - peak) / (1.0 - peak))


def _wave(u: float) -> float:
    """0 -> 1 -> 0 的余弦波，两端恰好是 0 且一阶导也是 0（接缝处不会有折角）。"""
    return (1.0 - math.cos(u * 2.0 * math.pi)) / 2.0


def transform_phase(ms: float):
    """把「变身演了多久」映射成 (阶段名, 阶段内进度 0~1)。

    纯函数，理由跟 `bounce_curve` 一样：不然验一次变身就得真等 7 秒。
    """
    ms = max(0.0, float(ms))
    if ms < TF_CHARGE_MS:
        return "charge", ms / TF_CHARGE_MS
    ms -= TF_CHARGE_MS
    if ms < TF_BURST_MS:
        return "burst", ms / TF_BURST_MS
    ms -= TF_BURST_MS
    if ms < TF_HOLD_MS:
        return "hold", ms / TF_HOLD_MS
    ms -= TF_HOLD_MS
    if ms < TF_FADE_MS:
        return "fade", ms / TF_FADE_MS
    return "done", 1.0


def particle_seed(i: int) -> float:
    """第 i 颗光点的固定相位，[0, 1)。

    用整数散列而不是 `random`：演示视频是**按帧重渲染**的，同一秒必须每次
    都长一样。用 random 的话每出一版片子光点位置都不一样，出了问题也没法
    对着两版比。跟 `bounce_curve` 可重放是同一个要求。
    """
    return ((i * 2654435761) % 4096) / 4096.0


# 整段变身的进度断点：0=蓄力起、1=爆发起、2=维持起、3=消退起、4=结束。
# 下标 0..4 分别对应 _sparks 里各关键帧表的第几项。
_T0 = 0.0
_T1 = TF_CHARGE_MS / TF_MS
_T2 = (TF_CHARGE_MS + TF_BURST_MS) / TF_MS
_T3 = (TF_CHARGE_MS + TF_BURST_MS + TF_HOLD_MS) / TF_MS
_T4 = 1.0

# 光点轨道的三条关键帧曲线，都是「整段进度 t -> 值」。
# **每一项的首尾由相邻关键帧共用**，连续性是结构上给的，不是每段各推公式凑的。
TF_SPARK_R = ((_T0, 0.50), (_T1, 0.24), (_T2, 0.50), (_T3, 0.42), (_T4, 0.30))
TF_SPARK_C = ((_T0, -0.44), (_T1, -0.42), (_T2, -0.52), (_T3, -0.78), (_T4, -0.95))
TF_SPARK_A = ((_T0, 0.0), (_T1, 0.85), (_T2, 0.85), (_T3, 0.85), (_T4, 0.0))


def _keys(tbl, t: float) -> float:
    """在关键帧表上取值，段内用 `_smooth` 缓动。

    `_smooth` 两端的导数都是 0，所以**每个内部关键帧左右导数都是 0**，
    接出来是 C1 连续的 —— 光点飞过关键帧时不会有折角。
    """
    if t <= tbl[0][0]:
        return tbl[0][1]
    for (t0, v0), (t1, v1) in zip(tbl, tbl[1:]):
        if t <= t1:
            k = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
            return v0 + (v1 - v0) * _smooth(k)
    return tbl[-1][1]


def tf_progress(phase: str, u: float) -> float:
    """阶段名 + 阶段内进度 -> 整段变身的进度 t（0..1）。"""
    base = {"charge": _T0, "burst": _T1, "hold": _T2, "fade": _T3}.get(phase, _T4)
    span = {"charge": _T1 - _T0, "burst": _T2 - _T1,
            "hold": _T3 - _T2, "fade": _T4 - _T3}.get(phase, 0.0)
    return min(1.0, base + u * span)


def _sparks(phase: str, u: float):
    """光点位置。坐标单位是**角色宽/高**，原点在她脚底中心，y 为负是在脚底以上。

    四拍走位：蓄力时从外面收进来（「光在聚拢」）、爆发时炸出去、维持时绕着
    她慢慢转、消退时飘散。收拢那一拍是必要的 —— 只有炸出去的话，它读起来是
    「她炸了」而不是「她在变身」。

    **整条轨道只写成「整段进度 t」的函数**，而不是每个阶段各写一套公式。
    各写一套的话，接缝处要对齐的量有五个（x、y、轨道半径、中心高度、透明度），
    漏掉任何一个都是**一帧之内光点整体瞬移**：第一版蓄力收到 1.0 倍半径、
    爆发却从 0.6 倍起步，接缝上一帧之内每颗光点横跳 0.16 个角色宽度（约 36px），
    50fps 下看得清清楚楚。单看每个阶段的代码都平滑，只有把边界两边各取一个值
    比一比才看得出来 —— 改成一条曲线之后，这类错在结构上就发生不了了。
    """
    t = tf_progress(phase, u)
    out = []
    for i in range(TF_PARTICLES):
        ang = particle_seed(i) * 2 * math.pi
        # 半径、大小、亮度都错开：不错开的话它们会在同一圈上排队站好，
        # 看着像一串珠子而不是一群光点。
        spread = 0.85 + 0.30 * ((i * 7919) % 100) / 100.0
        # **轨道半径封顶**：最外一圈 = 0.50 * 1.15 = 0.575 个角色宽度。
        # 窗口横向只有 disp_w/2 + 边距，按本素材的比例算约合 ±0.61 个 dest_w。
        # 第一版轨道写到 ±1.3，光点全在窗口外面被裁掉 —— 代码里一颗不少，
        # 渲染出来一颗看不见，而且不报任何错。
        r = _keys(TF_SPARK_R, t) * spread
        cy = _keys(TF_SPARK_C, t)
        # 转圈和「整段进度」走，不在阶段内归零 —— 归零的话每个接缝上角度都会
        # 倒回去一次，光点会原地打个哆嗦
        th = ang + t * 2.0 * math.pi * 1.15
        x = math.cos(th) * r
        y = cy + math.sin(th) * r * 0.42
        a = _keys(TF_SPARK_A, t) * (0.75 + 0.25 * ((i * 104729) % 100) / 100.0)
        # 直径 6~15px（按 dest_w 算）才看得见。第一版 0.012~0.025 是 1~2px，
        # 又被那张径向渐变自身的外圈淡出吃掉一半，等于没画。
        out.append((x, y, 0.030 + 0.045 * ((i * 31) % 7) / 7.0, max(0.0, a)))
    return out


def transform_visual(ms: float) -> dict:
    """变身到 `ms` 毫秒时的全部画法参数。纯函数，不碰 QObject。

    `ring` 是冲击环 `(半径倍数, 不透明度)`，半径单位是**角色宽度**；只有爆发
    那一拍有，其余是 None。

    `pop` 是额外放大比例。它跟 `TF_HOVER` 一起受 pet.py 的 `MARGIN_RATIO`
    约束 —— 那 6.5% 是按「呼吸 + 弹跳拉伸 + 腾空」算的，这里两个量加起来
    必须留在余量里，否则她窜起来那几帧呆毛会被窗口顶边裁掉（这个项目已经
    在弹跳上栽过一次，见 pet.py 的 MARGIN_RATIO 注释）。
    """
    phase, u = transform_phase(ms)
    v = {"phase": phase, "glow": 0.0, "ring": None, "tint": 0.0,
         "hops": 0.0, "pop": 0.0, "sparks": []}
    if phase == "done":
        return v

    if phase == "charge":
        # 蓄力：光聚拢、她微微下沉。下沉用 sin 而不是 smoothstep —— 收尾要回到 0，
        # 不然后面爆发那一拍开头接不上（见 _bump 的注释）。
        v["glow"] = 0.55 * _smooth(u)
        v["pop"] = -0.02 * math.sin(math.pi * u)
    elif phase == "burst":
        # 爆发：白光最亮、环扫出去、她放大一下。环在前段就扫完，别等这一拍走完
        v["glow"] = 0.55 + 0.45 * _bump(u)
        # 环**从她身上长出来**：半径起点几乎为 0、透明度从 0 快速拉上来。
        # 第一版是「半径 0.35、透明度 0.9」凭空出现 —— 蓄力那一拍还没有环，
        # 下一帧就多出一个 0.9 不透明度的圈，读起来是「画面里多了个东西」。
        # 前 12% 拉满就够了：够快，仍然像「炸出来」而不是「淡进来」。
        v["ring"] = (0.06 + 1.34 * _smooth(u),
                     0.95 * _smooth(min(1.0, u / 0.12)) * (1.0 - u) ** 1.5)
        # 染色快速拉满再回落一档 —— 这一下「变」要快，慢了就不像变身像渐变色
        v["tint"] = TF_TINT * _smooth(min(1.0, u / 0.12)) * (1.0 - 0.25 * _smooth(u))
        v["pop"] = TF_POP * _bump(u)
        v["hops"] = TF_HOVER * _smooth(u)
    elif phase == "hold":
        # 亮着一会儿再动：变身靠「状态持续」区别于「闪了一下」。
        # 三个量都在「首尾 = 爆发结束值」的基础上起伏，接缝才接得上。
        v["glow"] = 0.55 + 0.17 * _wave(u)
        v["tint"] = TF_TINT * (0.75 + 0.25 * _wave(u))
        v["hops"] = TF_HOVER * (1.0 + 0.5 * math.sin(math.pi * u))
    else:                                     # fade
        k = 1.0 - _smooth(u)
        v["glow"] = 0.55 * k
        v["tint"] = TF_TINT * 0.75 * k
        v["hops"] = TF_HOVER * k
    v["sparks"] = _sparks(phase, u)
    return v


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

        # 变身进度（毫秒）。None = 没在变身。跟跳舞一样用「已经演了多久」
        # 现算而不是每帧累加，理由见 frame_index()。
        self._tf = None

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
        #
        # 变身期间同理，而且更硬：那时候她的纵向位移已经被变身接管（悬浮 +
        # 爆发那一下的放大），再叠一层弹跳就是两种位移相加，呆毛会顶出边距。
        if self.dancing or self.transforming:
            return
        self._bounce = 0.0

    @property
    def bouncing(self) -> bool:
        return self._bounce < 1.0

    @property
    def dancing(self) -> bool:
        return self._dance_frames > 0

    @property
    def transforming(self) -> bool:
        return self._tf is not None

    def transform(self) -> None:
        """开始变身。再点一次就重头演，跟 `dance()` 同一个约定。

        跳舞期间直接忽略：那些帧是完整动作、按人物底部对齐画的，上面再浮一层
        光环和染色，等于给一段跳着的舞贴了张会动的贴纸。
        """
        if self.dancing:
            return
        self._tf = 0.0
        self._bounce = 1.0        # 变身接管位移，别让弹跳半路插进来
        self.frame.emit()

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
        elif self.transforming:
            self._tf += FPS_MS
            if self._tf >= TF_MS:
                self._tf = None       # 演完了，自动回到平常状态
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

    @property
    def transform_pose(self):
        """变身那一层要画的东西（dict），没在变身就是 None。

        是 property 不是方法 —— `gaze_pose` 就是在这个上头栽过一次：
        pet.py 里写成 `gaze_pose()` 得到 `TypeError: 'tuple' object is not
        callable`，而在 paintEvent 里抛异常 Qt 会**直接打死进程**（退出码 127，
        没有任何 traceback）。
        """
        if not self.transforming:
            return None
        return transform_visual(self._tf)

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
        # 变身期间她**悬浮**（hops）并且爆发那一下**放大**（pop）。两个都只往
        # 上走，所以吃的是窗口上边距。弹跳那一路这时候是停的（transform() 把
        # _bounce 归了 1.0，pounce() 也拒收），不会两边位移相加。
        tf = self.transform_pose
        if tf is not None:
            dy -= tf["hops"]
            sy += tf["pop"]
            sx += tf["pop"]
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
