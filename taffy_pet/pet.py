"""桌宠主窗口：无边框、透明、置顶，可拖动。

点击 = 说话 + 弹余额 + 播音效 + 弹一下。拖拽和点击要靠位移量区分 ——
否则想挪个位置就会触发一次说话。
"""
import json
import random

from PyQt5.QtCore import Qt, QRectF, QTimer
from PyQt5.QtGui import (QBrush, QColor, QCursor, QGradient, QPainter, QPen,
                         QPixmap, QRadialGradient)
from PyQt5.QtWidgets import QApplication, QInputDialog, QLineEdit, QMenu, QWidget

from . import config as cfgmod
from . import paths
from .anim import HOP, PetAnimator, idle_delay, pick_idle_action
from .balance import BalanceFetcher
from .paths import ASSETS
from .toast import Toast
from .voice import GREETING, Voice

# 边距是角色显示高度的比例，不是固定像素 —— 角色的放大/弹跳都是按比例缩放的，
# 固定边距在角色调小之后会显得过大、调大之后又不够。
#
# 这个比例的来历：呼吸(+0.8%) 叠上弹跳拉伸(+3.0%) 是 3.8%，再加腾空（anim.py 的
# HOP，1.6%），合起来 5.4%，留到 6.5% 有余量。小了呆毛会在弹起那几帧被窗口顶边裁掉。
#
# 别再写「边距会挡住周围桌面图标」了 —— 量过，不会：窗口是带 alpha 的 layered window，
# 透明像素点得穿（WindowFromPoint 打在边距上返回的是下层窗口）。边距几乎不要钱。
# tools/smoke.py 会走完整条弹跳曲线来验这个余量够不够。
MARGIN_RATIO = 0.065
CLICK_SLOP = 6       # 位移小于这个像素数还算点击
CLICK_MS = 500       # 按下超过这么久算长按，不算点击

# 脚下那个影子。它不参与任何动画计算，存在的唯一理由是让人读出**她是站在
# 桌面上、不是贴在桌面上** —— 素材本身都是悬空的，没有地面参考时她怎么看
# 都像浮着。腾空时影子同时缩小和变淡，这是整幅画里唯一说明「离地多高」的线索。
# 影子宽度是照着**量出来的站姿**定的：立绘取景框贴着内容裁（见 dance_extract.py），
# 最底下 3% 那一带（就是靴底）占立绘宽度的 42%，往下 10% 那一带是 51%。
# 影子比站姿宽一截才露得出边 —— 卡在 51% 附近的话，它整个藏在靴子后面，
# 只在两腿之间透出一小块，读起来是「地上有块灰」而不是「她站在这儿」。
SHADOW_W_RATIO = 0.80      # 影子宽度 / 角色显示宽度
SHADOW_H_RATIO = 0.16      # 影子高度 / 影子宽度
SHADOW_ALPHA = 0.30        # 落地时影子中心的不透明度
SHADOW_LIFT_SHRINK = 0.35  # 跳到最高点时影子缩掉的比例
SHADOW_LIFT_FADE = 0.45    # 跳到最高点时淡掉的比例

# 鼠标离她多远算「完全朝那边看」，单位是**窗口宽度的倍数**。
# 写死像素不行：窗口宽度跟着 config 的 height 走，把角色调大一点，
# 「完全转向」的距离就该跟着变远，否则一大就再也不转了。
GAZE_REACH = 3.0

# 变身用的金。挑金色而不是粉色，是因为她本来就是粉的 —— 同色系的光打在她
# 身上看不出来，读起来只是「这张图变亮了」。金色跟她的粉发、棕裙子都岔开，
# 而且浅色桌面和深色桌面上都立得住。
TF_GLOW_RGB = (255, 214, 130)
TF_TINT_RGB = (255, 232, 186)
# 轮廓光比身上那层**饱和得多**。它只露在她轮廓外面，画在透明上，
# 所以是什么色就显示什么色 —— 用身上那层淡金只会得到一圈奶白。
TF_RIM_RGB = (255, 198, 88)

# 轮廓光的三圈：横向/纵向放大倍数 + 相对亮度。纵向一律压得比横向小 ——
# 素材顶到窗口上沿，纵向没有余量（见 paintEvent 里的说明）。
TF_RIM = ((1.06, 1.020, 0.95), (1.12, 1.045, 0.50), (1.19, 1.065, 0.26))


class PetWindow(QWidget):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool |
                            Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("塔菲")
        self.setMouseTracking(True)

        self.pix = self._load("taffy.png")
        self._blink_pix = self._load("taffy_blink.png")
        self.has_blink_asset = self._blink_pix is not None
        self.pix_blink = self._blink_pix or self.pix
        if self.pix is None:
            raise SystemExit("assets/taffy.png 不存在，先跑 python tools/build_assets.py")

        self.setWindowOpacity(float(cfg.get("opacity", 1.0)))

        self.pix_dance, self.dance_meta, self.dance_fw, self.dance_fh = self._load_dance()

        # 目标显示高度是「逻辑像素」。资源本身是高分辨率（见 build_assets.py），
        # 这里按比例缩到 height 逻辑像素 —— 高 DPI 屏上会自动铺满对应的物理像素。
        #
        # 之前这里直接 1:1 把资源画出去，结果 200% 缩放的屏上角色占了整屏 95% 高度，
        # 头被顶到屏幕外，看着像「不在桌面上」。
        self.disp_h = max(40.0, float(cfg.get("height", 200)))
        # 窗口宽度按**所有素材里最宽的那个**定，然后每种素材各自居中画。
        # 跳舞那几帧她是张开手的，比立绘宽 23%；按立绘定宽的话她一抬手两边就被切掉。
        # 多出来的那一截是透明像素 —— 透明处点得穿（见 MARGIN_RATIO 的注释），
        # 代价只是窗口矩形大了十几个像素，不用改窗口大小、不用重算位置。
        self.sprite_ar = self.pix.width() / self.pix.height()
        self.dance_ar = (self.dance_fw / self.dance_fh) if self.pix_dance else 0.0
        self.disp_w = max(self.sprite_ar, self.dance_ar) * self.disp_h
        self.margin = max(6.0, self.disp_h * MARGIN_RATIO)
        self.resize(int(round(self.disp_w + self.margin * 2)),
                    int(round(self.disp_h + self.margin * 2)))

        self.toast = Toast()
        self.fetcher = None
        self._press = None
        self._press_t = None
        self._moved = False

        self.animator = PetAnimator(bool(cfg.get("blink", False)))
        # 先接朝向、再接 update：信号按**连接顺序**触发，反过来的话画这一帧用的
        # 是上一帧算出来的朝向，她永远慢半拍。
        self.animator.frame.connect(self._update_gaze)
        self.animator.frame.connect(self.update)
        self.animator.start()

        self._shadow = self._make_shadow()
        # 变身的素材，都是一次性烘好的：一颗金色光点（缩小了当飞的光点），
        # 以及她的剪影 —— 剪影有两个用处，贴着她的轮廓放大一圈是**轮廓光**，
        # 原尺寸叠在身上是**染色**。两者要的颜色不一样，所以各烘一份。
        self._glow = self._make_glow()
        self._tint_pix = self._make_tint(self.pix, TF_TINT_RGB)
        self._tint_blink = self._make_tint(self.pix_blink, TF_TINT_RGB)
        # 轮廓光单独一张、颜色也更饱和：它叠在**透明**上（只露出她轮廓外面的
        # 那一圈），所以画什么色就是什么色，用身上那层淡金就只剩一圈奶白。
        self._rim_pix = self._make_tint(self.pix, TF_RIM_RGB)
        self._rim_blink = self._make_tint(self.pix_blink, TF_RIM_RGB)

        # 她自己找事做。存在的理由见 anim.IDLE_ACTIONS —— 简言之：没人会
        # 对桌宠右键，所以跳舞得她自己演。
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._idle_act)
        self._restart_idle()

        self._sound = self._load_sound()
        # 语音库归 Pet 持有，聊天窗只管调用 —— 音量配置只有一份（cfg["volume"]），
        # 两处各建一个播放器的话改音量就会只改到一半。
        self.voice = Voice(self.cfg)
        self._restore_pos()

    # ---------- 载入 ----------
    def _load(self, name: str):
        p = ASSETS / name
        if not p.exists():
            return None
        pm = QPixmap(str(p))
        return pm if not pm.isNull() else None

    def _load_dance(self):
        r"""载入跳舞精灵表，返回 (像素图, 元数据, 帧宽, 帧高)；缺任何一样就是四个空。

        `%APPDATA%\TaffyPet\dance\` 里的两个文件：

            dance.png    所有帧**横排**在一张图里，带 alpha
            dance.json   {"frames": 20, "frame_w": 227, "frame_h": 400,
                          "fps": 15.0, "pingpong": true, ...}

        第 i 帧的取景框就是 `(i * frame_w, 0, frame_w, frame_h)`。
        `pingpong` 为真表示这张表自带往返，播到头循环回去不会跳 ——
        这是**出素材那一步**接好的，播放端不用管。

        **读坏了只当没有，不抛异常** —— 跳舞是附加值，不能因为它让桌宠起不来。
        但损坏和缺失要分开说：文件在而内容不对是用户该知道的事（他可能装错了），
        文件压根不在是正常的（仓库里本来就不带，见 paths.DANCE_DIR）。
        """
        # 走 paths.DANCE_DIR 而不是 `from .paths import DANCE_DIR` —— 跟 voice.py
        # 一样在**调用时**取。导入时就绑死的话，测试和冒烟脚本就没法把它指到
        # 临时目录，只能往真实的数据目录里塞东西。
        d = paths.DANCE_DIR
        png, meta_p = d / "dance.png", d / "dance.json"
        if not png.exists() or not meta_p.exists():
            return None, None, 0, 0
        pm = self._load(str(png))
        if pm is None:
            print(f"[dance] {png} 打不开，这次就不跳舞了")
            return None, None, 0, 0
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            n, fw, fh = (int(meta["frames"]), int(meta["frame_w"]),
                         int(meta["frame_h"]))
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"[dance] {meta_p} 读不动（{type(e).__name__}: {e}），这次就不跳舞了")
            return None, None, 0, 0
        # 精灵表比元数据说的还小的话，取帧就会取到图外面去（drawPixmap 会拿到
        # 一块空的，看着像她突然消失）。这种不一致当损坏处理。
        if n <= 0 or fw <= 0 or fh <= 0 or pm.width() < n * fw or pm.height() < fh:
            print(f"[dance] 精灵表 {pm.width()}x{pm.height()} 跟 dance.json 说的 "
                  f"{n} 帧 x {fw}x{fh} 对不上，这次就不跳舞了")
            return None, None, 0, 0
        return pm, meta, fw, fh

    def _load_sound(self):
        p = ASSETS / "sounds" / "click.wav"
        if not p.exists():
            return None
        from PyQt5.QtMultimedia import QSoundEffect
        from PyQt5.QtCore import QUrl
        eff = QSoundEffect(self)
        eff.setSource(QUrl.fromLocalFile(str(p)))
        eff.setVolume(float(self.cfg.get("volume", 1.0)))
        return eff

    def play_sound(self) -> None:
        if self._sound is not None:
            self._sound.play()

    def _restore_pos(self) -> None:
        pos = self.cfg.get("pos")
        screen = QApplication.primaryScreen().availableGeometry()
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            x, y = self._clamp(int(pos[0]), int(pos[1]), screen)
            self.move(x, y)
            return
        # 默认落在右下角，但要夹在屏幕内 —— 算出来是负坐标时角色会被顶到屏幕外，
        # 看着就像「没启动」。
        self.move(*self._clamp(screen.right() - self.width() - 60,
                               screen.bottom() - self.height() - 10, screen))

    def _clamp(self, x: int, y: int, screen) -> tuple:
        """保证窗口至少有相当一部分留在屏幕内，别整只跑到屏幕外面去。"""
        x = max(screen.left() - self.width() // 3,
                min(x, screen.right() - self.width() * 2 // 3))
        y = max(screen.top() - self.height() // 3,
                min(y, screen.bottom() - self.height() * 2 // 3))
        return x, y

    # ---------- 绘制 ----------
    def _make_shadow(self) -> QPixmap:
        r"""烘一张脚下的软影子，只做一次。

        做成椭圆的**径向渐变**（`ObjectBoundingMode` 会让渐变跟着外接矩形
        拉伸，所以画的虽然是椭圆，等值线也是椭圆的，不会有硬边）。
        烘成 pixmap 而不是每帧现画：形状永远一样，每帧变的只是缩放和透明度，
        50fps 下每秒重建 50 次渐变是白花的。

        颜色用纯黑。半透明黑在白色桌面和深色桌面上都读作「影子」，
        而带一点色调的（比如偏紫）在白底上会像污渍。
        """
        w, h = 256, 64
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        g = QRadialGradient(0.5, 0.5, 0.5)
        g.setCoordinateMode(QGradient.ObjectBoundingMode)
        g.setColorAt(0.0, QColor(0, 0, 0, 255))
        g.setColorAt(0.45, QColor(0, 0, 0, 150))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        # 内缩 1px：抗锯齿的边缘正好落在边界上会被裁掉，看着像刀切的
        p.drawEllipse(QRectF(1, 1, w - 2, h - 2))
        p.end()
        return pm

    def _make_glow(self) -> QPixmap:
        r"""烘一颗金色的光点。变身时飞的那些就是它缩小画的。

        跟影子同一条理由：形状永远一样，每帧变的只有大小和透明度。现画渐变的话
        14 个光点每帧就是 14 次渐变重建，50fps 下每秒 700 次，白花的。

        **中心不能再偏白了**。它以前还兼着「背后那团光晕」的活儿，那时候心偏白
        是对的（一大团纯金会发闷）；现在只用来画光点，而光点是画在**桌面上**的
        ——浅色的壁纸本来就是白的，奶白的点落在上面直接看不见。饱和的金才有对比。
        """
        n = 256
        pm = QPixmap(n, n)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        g = QRadialGradient(0.5, 0.5, 0.5)
        g.setCoordinateMode(QGradient.ObjectBoundingMode)
        g.setColorAt(0.0, QColor(255, 244, 190, 255))
        g.setColorAt(0.30, QColor(255, 216, 120, 235))
        g.setColorAt(0.65, QColor(255, 190, 80, 90))
        g.setColorAt(1.0, QColor(255, 180, 60, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        # 内缩 1px：抗锯齿的边缘正好压在边界上会被裁掉，看着像刀切的
        p.drawEllipse(QRectF(1, 1, n - 2, n - 2))
        p.end()
        return pm

    def _make_tint(self, src: QPixmap, rgb: tuple) -> QPixmap:
        r"""把一张立绘压成**纯色剪影**（保留原来的 alpha）。

        `CompositionMode_SourceIn`：只保留「源」里目标 alpha 不为 0 的地方，
        也就是拿她的轮廓当模子，把整块颜色裁成她的形状。

        为什么不直接画一块半透明色矩形盖上去：那会把窗口里她周围的透明区域
        也一起染了，等于给整只角色套了个方形的黄色罩子。

        **传参而不是直接读 self.pix**：闭眼那张的剪影得跟闭眼那张配。拿睁眼的
        剪影盖在闭眼立绘上，变身的几百毫秒里她会睁着眼眨一次。

        `rgb` 也是参数：身上那层是淡金、轮廓光那层是饱和金，两处要的颜色不一样。
        """
        pm = QPixmap(src.size())
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.drawPixmap(0, 0, src)
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(pm.rect(), QColor(*rgb))
        p.end()
        return pm

    def _update_gaze(self) -> None:
        r"""把「鼠标在她哪一侧」告诉动画器。

        用**全局**坐标，不是窗口内坐标：鼠标移出她的窗口之后她还得继续看着
        （那才是「她在看着你」），窗口内坐标一出界就没意义了。

        只在动画节拍上算，不挂鼠标事件：鼠标在**别的窗口上**移动时这里一个
        事件都收不到，挂事件的话她只在自己窗口里跟得上。
        """
        cx = self.x() + self.width() / 2.0
        reach = max(1.0, self.width() * GAZE_REACH)
        self.animator.set_gaze((QCursor.pos().x() - cx) / reach)

    def paintEvent(self, _event) -> None:
        sx, sy, dy, blinking, dance_i = self.animator.state()
        # 是 property 不是方法 —— 写成 `gaze_pose()` 是 TypeError，而在 paintEvent
        # 里抛异常不是弹个 traceback 就算了：Qt 会把进程直接打死（退出码 127），
        # 从外面看跟崩溃一模一样。
        lean_deg, shift = self.animator.gaze_pose

        if dance_i is None:
            src = self.pix_blink if blinking else self.pix
            ar, src_rect = self.sprite_ar, QRectF(src.rect())
        else:
            # 从精灵表里取出这一帧。整张表只解码一次（self.pix_dance），
            # 每帧只是换个取景框 —— 跟立绘同一套画法，不用逐帧解码二十个文件。
            src = self.pix_dance
            ar = self.dance_ar
            src_rect = QRectF(dance_i * self.dance_fw, 0,
                              self.dance_fw, self.dance_fh)

        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        m = self.margin
        # 窗口是按最宽的素材定的，窄的那个要居中画 —— 都靠左的话她会看着偏在
        # 窗口一边，拖动时手感和落点对不上。
        dest_w = ar * self.disp_h
        left = m + (self.disp_w - dest_w) / 2.0
        # 变身那一层。**是 property，别写成 tf()** —— 同 `gaze_pose`，多一对括号
        # 就是 TypeError，而 paintEvent 里的异常会被 Qt 变成静默退出码 127。
        tf = self.animator.transform_pose
        ax, ay = w / 2.0, h - m               # 脚底中心，光晕/环/光点都锚在这

        # 影子先画。它**不跟着倾斜**：影子是落在地面上的东西，跟着人一起歪
        # 就变成贴在她身上的一块黑。横向跟着 shift 走一点，但幅度比人小一半，
        # 那点差值就是「她在动、地面没动」。
        #
        # 变身时她悬浮（dy 里多了 hops），这里照样按 -dy/HOP 算离地比例，
        # 于是影子自己就跟着缩小变淡 —— 「离地多高」这条线索是同一个，
        # 不用给变身单开一套。
        lift = max(0.0, -dy / HOP) if HOP else 0.0
        sw = dest_w * SHADOW_W_RATIO * (1.0 - SHADOW_LIFT_SHRINK * lift)
        if sw >= 2.0:
            sh = sw * SHADOW_H_RATIO
            p.setOpacity(SHADOW_ALPHA * (1.0 - SHADOW_LIFT_FADE * lift))
            # 必须给三个参数：Qt 没有 `drawPixmap(QRectF, QPixmap)` 这个重载
            # （两参数那个只吃 QRect），少写一个源矩形是 TypeError。
            p.drawPixmap(QRectF(w / 2.0 + shift * dest_w * 0.5 - sw / 2.0,
                                h - m - sh, sw, sh),
                         self._shadow, QRectF(self._shadow.rect()))
            p.setOpacity(1.0)

        # 冲击环。在影子之后、人之前 —— 它得像是从她身上扫出来的，
        # 压在人后面才有「从这儿发出去」的方向感。
        if tf is not None and tf["ring"] is not None:
            rad, alpha = tf["ring"]
            r = rad * dest_w
            if r > 2.0 and alpha > 0.01:
                pen = QPen(QColor(*TF_GLOW_RGB, 255))
                # 往外扫的同时变细：冲击波散开就是会变薄，等宽看着像呼啦圈
                pen.setWidthF(max(1.5, dest_w * 0.045 * (1.0 - min(1.0, rad / 2.3))))
                p.setOpacity(alpha)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QRectF(ax - r, ay - self.disp_h * 0.5 - r,
                                     r * 2.0, r * 2.0))
                p.setOpacity(1.0)

        p.save()
        p.translate(w / 2.0, h - m)           # 锚点：底部中心
        # 倾斜也绕这个锚点 —— 绕腰或绕中心的话脚会离开地面，看着像飘着转
        p.rotate(lean_deg)
        p.translate(shift * dest_w, 0.0)
        p.scale(sx, sy)
        p.translate(-w / 2.0, -(h - m) + dy * self.disp_h)

        # 轮廓光：把她的剪影放大一圈、用**加法**叠在人后面，露在她边上的那一圈
        # 就是「她在发光」。
        #
        # 为什么不做成一团圆光晕垫在背后（第一版就是）：窗口只比角色宽出 margin
        # （6.5%）。任何在窗口边上还有可见亮度的圆形光晕，落到桌面上看都是一个
        # **发光的方块** —— 渐变是被窗口矩形硬切掉的；而缩到能整团塞进窗口，
        # 它又比人还窄，整个被挡在身后，等于没画。贴着轮廓的一圈光没这个问题，
        # 它的形状就是她本人，切不出直边。
        #
        # 用加法而不是直接盖色：盖色（SourceOver）会拿淡金换掉她的脸、头发、
        # 裙子，整个人变成一块平的色斑，素材白瞎了。加法只往上加亮，暗处染金、
        # 亮处更亮，五官和衣服的层次都还在。
        #
        # 三圈递减而不是一圈：单圈就是一条硬边的金线，像贴纸描边；叠三圈拉开
        # 层次才像光散出来。三张 drawPixmap，跟原来一团渐变一个量级。
        #
        # 放大倍数卡在 1.07 以内是**边界**逼出来的：她的呆毛顶在素材最上沿，
        # 纵向放大 7% 就顶到窗口上边（margin 正好 6.5%）。
        if tf is not None and tf["glow"] > 0.01 and dance_i is None:
            rim = self._rim_blink if blinking else self._rim_pix
            for kx, ky, ka in TF_RIM:
                rw, rh = dest_w * kx, self.disp_h * ky
                p.setCompositionMode(QPainter.CompositionMode_Plus)
                p.setOpacity(tf["glow"] * ka)
                # 以**脚底中心**为锚点放大：绕中心放大的话脚会离地，她看着在飘
                p.drawPixmap(QRectF(left - (rw - dest_w) / 2.0,
                                    m + self.disp_h - rh, rw, rh),
                             rim, src_rect)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setOpacity(1.0)

        p.drawPixmap(QRectF(left, m, dest_w, self.disp_h), src, src_rect)
        # 染色盖在她身上，**必须跟立绘共用同一套变换和目标矩形**：另外算一遍的话
        # 两份矩形差半个像素，边上就会露出一圈原来的颜色，像描了道脏边。
        # 所以它在 save/restore 里面，而且直接复用 left/m/dest_w/disp_h。
        if tf is not None and tf["tint"] > 0.01 and dance_i is None:
            p.setOpacity(tf["tint"])
            p.drawPixmap(QRectF(left, m, dest_w, self.disp_h),
                         self._tint_blink if blinking else self._tint_pix,
                         src_rect)
            p.setOpacity(1.0)
        p.restore()

        # 光点最后画，压在整个人**上面**。放背后的话会被裙子挡掉大半，
        # 只剩边缘几颗露着，看着像画面脏了而不是在发光。
        if tf is not None:
            sr = QRectF(self._glow.rect())
            for sx_, sy_, rr, aa in tf["sparks"]:
                if aa <= 0.01:
                    continue
                rad = max(1.5, rr * dest_w)
                px_, py_ = ax + sx_ * dest_w, ay + sy_ * self.disp_h
                p.setOpacity(aa)
                p.drawPixmap(QRectF(px_ - rad, py_ - rad, rad * 2.0, rad * 2.0),
                             self._glow, sr)
            p.setOpacity(1.0)

    # ---------- 交互 ----------
    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        self._press = e.globalPos()
        self._offset = e.globalPos() - self.frameGeometry().topLeft()
        self._press_t = QTimer()          # 只用来判断是不是长按
        self._press_t.start(CLICK_MS)
        self._moved = False
        # 他一碰她，闲置计时就从头算 —— 「她自己找事做」的前提是**没人理她**。
        self._restart_idle()

    def mouseMoveEvent(self, e) -> None:
        if self._press is None:
            return
        if (e.globalPos() - self._press).manhattanLength() > CLICK_SLOP:
            self._moved = True
            self.move(e.globalPos() - self._offset)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton or self._press is None:
            return
        long_press = self._press_t is not None and not self._press_t.isActive()
        was_click = (not self._moved) and (not long_press)
        self._press = None
        if self._press_t:
            self._press_t.stop()
            self._press_t = None
        if self._moved:
            self._remember_pos()
        if was_click:
            self.on_click()

    def mouseDoubleClickEvent(self, e) -> None:
        """双击她 = 找她说话。只认左键：右键双击是菜单那一路，不该顺手弹出窗口。

        已知瑕疵：第一下已经把 mouseReleaseEvent 的单击逻辑走完了，所以窗口开出来
        之前她会先弹一句气泡、查一次余额。要消掉只能给每次单击加 250ms 延迟等第二下
        落空，那更烦 —— 单击是她最主要的交互。接受。
        """
        if e.button() == Qt.LeftButton:
            self.open_chat()

    def show_hint_if_first_run(self) -> None:
        """透明窗口没有任何可见的边框，不提示的话没人知道能右键、能拖。"""
        if self.cfg.get("hint_shown"):
            return
        self.toast.show_message("拖我换位置 · 右键点我打开菜单",
                                "没人理我的时候，我自己会动")
        self.toast.anchor_above(self.frameGeometry())
        self.cfg["hint_shown"] = True
        cfgmod.save(self.cfg)

    def on_click(self) -> None:
        self.play_sound()
        self.animator.pounce()
        # 点她 = 打招呼。`speech` 是用户自己配的文案，跟语音库的触发词没有约定关系，
        # 所以它只是首选，挑不出来退到 GREETING —— 到此为止的话，用户点她永远没声，
        # 而「点她」是他判断这桌宠到底有没有语音的唯一途径。
        self.voice.play(self.cfg.get("speech", ""), GREETING)
        self.toast.show_message(self.cfg.get("speech", ""), "余额查询中…")
        self.toast.anchor_above(self.frameGeometry())
        self.refresh_balance()

    def build_menu(self) -> QMenu:
        """单独拆出来是为了能自动测 —— exec_() 会阻塞，没法在测试里直接调。"""
        m = QMenu(self)
        m.addAction("和她说话", self.open_chat)
        m.addAction("编辑人设", self.edit_persona)
        m.addSeparator()
        m.addAction("设置 API Key", self.ask_api_key)
        m.addAction("刷新余额", self.refresh_balance)
        m.addAction("清空对话记录", self.clear_chat)
        m.addSeparator()

        dance = m.addAction("跳个舞")
        # 和「说话出声」同一个处理：没素材就灰掉并写明缺什么，
        # 而不是让用户点了没反应 —— 在他那边「点了没反应」和「坏了」是一回事。
        if self.pix_dance is None:
            dance.setEnabled(False)
            dance.setText("跳个舞（没装舞蹈素材）")
        dance.triggered.connect(self.do_dance)

        tf = m.addAction("变身")
        tf.setToolTip("她整个人泛金光、浮起来几秒。没人理她的时候偶尔也会自己来一次")
        tf.triggered.connect(self.do_transform)

        blink = m.addAction("眨眼")
        blink.setCheckable(True)
        blink.setChecked(self.animator.blink_enabled)
        blink.setEnabled(self.has_blink_asset)
        if not self.has_blink_asset:
            blink.setText("眨眼（缺 taffy_blink.png）")
        blink.toggled.connect(self._set_blink)

        top = m.addAction("窗口置顶")
        top.setCheckable(True)
        top.setChecked(bool(self.cfg.get("always_on_top", True)))
        top.toggled.connect(self.set_always_on_top)

        say = m.addAction("说话出声")
        say.setCheckable(True)
        say.setChecked(bool(self.cfg.get("voice", True)))
        # 没有语音库就把这一项灰掉并说明原因，而不是让用户点了没反应 ——
        # 「点了没反应」和「功能坏了」在他的角度是一回事。
        if not self.voice.entries:
            say.setEnabled(False)
            say.setText("说话出声（没装语音库）")
        say.toggled.connect(self._set_voice)

        idle = m.addAction("自己找事做")
        idle.setCheckable(True)
        idle.setChecked(bool(self.cfg.get("idle", True)))
        idle.setToolTip("没人理她的时候，过一会儿她自己会跳舞、说话或者蹦一下")
        idle.toggled.connect(self._set_idle)

        m.addSeparator()
        m.addAction("退出", self.quit)
        return m

    def contextMenuEvent(self, e) -> None:
        self.build_menu().exec_(e.globalPos())

    # ---------- 菜单动作 ----------
    def do_dance(self) -> None:
        if self.pix_dance is None:
            return
        # 帧率来自 dance.json 而不是写死在 anim.py：换一段素材、重新出一张表，
        # 拍子就该跟着新素材走，不该还按旧的那段跳。
        self.animator.dance(int(self.dance_meta["frames"]),
                            float(self.dance_meta.get("fps", 15.0)))

    def do_transform(self) -> None:
        """变身。不需要任何素材 —— 光晕、冲击环、光点、染色都是画出来的。

        素材只用到立绘本身，所以没有 `pix_dance is None` 那种「没装就不能用」
        的分支，菜单项永远可点。
        """
        self.animator.transform()

    # ---------- 她自己找事做 ----------
    def _restart_idle(self) -> None:
        r"""重新排一次「她自己找事做」。

        每次都显式重排，不用 setInterval：间隔是随机的（`anim.idle_delay`），
        固定周期会变成「每 45 秒她动一下」的节拍器，几天下来比不动还假。

        用户在做什么就先不作数 —— 每次交互都调一遍这个，等于「他不动了我才动」。
        """
        if not self.cfg.get("idle", True):
            self._idle_timer.stop()
            return
        self._idle_timer.start(int(idle_delay(random.random()) * 1000))

    def _idle_act(self) -> None:
        r"""闲置够久了：她自己挑一件事做，然后把定时器排回下一次。

        **先排定时器、再做事**：`do_dance` 这一路要读素材，真抛了异常的话，
        排在后面就把这个功能永久停掉了 —— 而且从桌面上完全看不出来，她只是
        从此不再自己动。排在前面，最坏也只是这一轮没演成。
        """
        self._restart_idle()

        # 手正按着她（在拖、在长按）就别演：他会觉得她在跟他的手打架。
        if self._press is not None:
            return
        # 聊天窗开着说明他正在跟她说话，这时候她自己蹦一句是抢戏。
        chat = getattr(self, "chat", None)
        if chat is not None and chat.isVisible():
            return

        action = pick_idle_action(random.random())
        if action == "dance" and self.pix_dance is None:
            # 没装舞蹈素材时「跳舞」退化成蹦一下，而不是什么都不发生 ——
            # 抽到跳舞的那一轮空转，在用户看来就是「说好的自己动呢」。
            action = "hop"

        if action == "dance":
            self.do_dance()
            return

        # 变身也是「整段演出」，跟跳舞一样直接走人 —— 底下那套「弹一下再说话」
        # 是给 say / hop 用的，套在变身后面等于变完身再蹦一下。
        if action == "transform":
            self.do_transform()
            return

        # 「说话」和「蹦」都从弹一下开始 —— 只有气泡没有动作的话，静音用户看到的
        # 是「凭空冒出个气泡」，一秒钟都撑不住。差别只在说不说那句话。
        self.animator.pounce()
        if action != "say":
            return

        entry = self.voice.random_line()
        if entry is None:
            return                       # 没装语音库：已经弹过了，这一轮就算了
        self.voice.play_entry(entry)
        # 她说了什么得看得见 —— 否则静音用户看到的是「她莫名其妙弹了一下」。
        text = str(entry.get("text", "")).strip()
        if text:
            self.toast.show_message(text)
            self.toast.anchor_above(self.frameGeometry())

    def _set_blink(self, on: bool) -> None:
        self.cfg["blink"] = bool(on)
        self.animator.set_blink_enabled(bool(on))
        cfgmod.save(self.cfg)

    def _set_idle(self, on: bool) -> None:
        self.cfg["idle"] = bool(on)
        cfgmod.save(self.cfg)
        # 关掉时先把已经排上的那一次取消（`_restart_idle` 里 stop 的就是它），
        # 否则关掉之后最多还会再演一次，看着像开关没生效。
        self._restart_idle()

    def _set_voice(self, on: bool) -> None:
        self.cfg["voice"] = bool(on)
        cfgmod.save(self.cfg)
        # 打开时给一声反馈：不然用户不知道该不该相信它生效了。
        # 关掉时**不播** —— 刚说「别出声」又响一下是最容易让人上火的细节。
        if on:
            self.voice.play("在呢在呢")

    def set_always_on_top(self, on: bool) -> None:
        self.cfg["always_on_top"] = bool(on)
        flags = self.windowFlags()
        self.setWindowFlags(flags | Qt.WindowStaysOnTopHint if on
                            else flags & ~Qt.WindowStaysOnTopHint)
        self.show()
        cfgmod.save(self.cfg)

    def open_chat(self) -> None:
        """已经开着就叫到前面，不要再开一个。"""
        if getattr(self, "chat", None) is None:
            from .chat_window import ChatWindow
            self.chat = ChatWindow(self.cfg, voice=self.voice)
        self.chat.show_and_raise()
        # 开聊也算「有人理她」，闲置计时从头算。
        self._restart_idle()

    def edit_persona(self) -> None:
        """先把窗开出来再编辑 —— 在记事本里改完切回来就能直接接着说。"""
        self.open_chat()
        self.chat.edit_persona()

    def clear_chat(self) -> None:
        self.open_chat()
        self.chat.clear_history()

    def ask_api_key(self) -> None:
        cur = self.cfg.get("api_key", "")
        shown = ("*" * len(cur[-6:]) + cur[-6:]) if len(cur) > 6 else cur
        text, ok = QInputDialog.getText(
            self, "设置 API Key",
            "粘贴 DeepSeek API Key（sk- 开头）：\n"
            f"当前来源：{cfgmod.api_key_source(self.cfg)}\n\n"
            "想用环境变量 DEEPSEEK_API_KEY 的话，把这里留空即可 —— 环境变量优先。",
            QLineEdit.Password, cur)
        if not ok:
            return
        self.cfg["api_key"] = text.strip()
        cfgmod.save(self.cfg)
        print(f"[apikey] 已保存（{shown or '空'} -> "
              f"{('*' * 6 + text.strip()[-4:]) if text.strip() else '空'}）")
        self.refresh_balance()

    # ---------- 余额 ----------
    def refresh_balance(self) -> None:
        if self.fetcher is not None and self.fetcher.isRunning():
            return
        key = cfgmod.api_key(self.cfg)
        if not key:
            self.toast.set_balance("未设置 API Key（右键设置）")
            return
        self.fetcher = BalanceFetcher(key, self)
        self.fetcher.ok.connect(lambda t: self.toast.set_balance(f"余额 {t}"))
        self.fetcher.fail.connect(lambda t: self.toast.set_balance(t))
        self.fetcher.start()

    # ---------- 收尾 ----------
    def _remember_pos(self) -> None:
        self.cfg["pos"] = [self.x(), self.y()]

    def _close_chat(self) -> None:
        """退出前把聊天窗收掉，否则那个 QThread 还挂在网络上。

        必须是带守卫的版本，别改成无条件 `self.chat = None`：`close()` 是同步走完
        closeEvent → `_stop_worker()` 的，而那里面有一支**有意**留着 worker 不放
        （线程卡在网络上、强杀也没杀掉，那儿有注释）。这时候把窗口的最后一个 Python
        引用丢掉，窗口就会析构，而 ChatWorker 是它的 Qt 子对象 —— QThread 析构时线程
        还在跑，Qt 直接 qFatal → abort(0xC0000409)。这正是 Task 5 复审抓出的 C1，
        而且 aboutToQuit 救不了：崩在 _close_chat() 里面，根本走不到 QApplication.quit()。
        """
        chat = getattr(self, "chat", None)
        if chat is None:
            return
        chat.close()
        if chat.worker is None:
            self.chat = None

    def quit(self) -> None:
        self._remember_pos()
        cfgmod.save(self.cfg)
        self.animator.stop()
        # 这一句在 cfgmod.save **之后**，所以 chat_geometry 不是上面那次存下来的，而是
        # 下面 close() 里 ChatWindow.closeEvent 自己那次 save 存的 —— 两处 save 各管
        # 各的，别为了「只存一次」把顺序理顺：closeEvent 里那次是「用户只关聊天窗、
        # 不退出程序」时唯一的落盘点，删掉它几何就丢了。
        self._close_chat()
        self.toast.hide()
        QApplication.quit()

    def closeEvent(self, e) -> None:
        self._remember_pos()
        cfgmod.save(self.cfg)
        self.animator.stop()
        self._close_chat()
        self.toast.close()
        super().closeEvent(e)
