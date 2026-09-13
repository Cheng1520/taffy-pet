# 开发笔记

面向想改代码、重新生成素材或者自己打包的人。只想用她的话看 [README](../README.md) 就够了。

## 从源码运行

需要一个 Python 3（开发环境是 3.13）。

```bash
pip install -r requirements.txt
python main.py
```

桌宠本身只要 `PyQt5` 和 `requests`。`requirements.txt` 里下面那行
（Pillow / numpy / scipy / opencv / imageio-ffmpeg）只有重新生成素材才用得上。

### 放个快捷方式到桌面上

```bash
python tools/make_shortcut.py
```

会在桌面生成「塔菲.lnk」，并顺手生成 `assets/taffy.ico`。

快捷方式指向 `pythonw.exe` 而不是 `python.exe` —— 后者会挂一个黑框控制台窗口在桌面上，
关掉它还会把塔菲一起带走。

### 同时开了两只？

不会。`main.py` 用 `QLocalServer` 起了个命名管道（`taffy-pet-single-instance`）做单实例保护：
第二次启动不会开新窗口，而是把已经开着的那只叫到前面来。

## 配置

配置文件的落点跟运行方式有关：

| 怎么跑的 | `config.json` / `taffy.log` 在哪 |
| --- | --- |
| `python main.py` | 项目根目录 |
| 装成安装包 | `%APPDATA%\TaffyPet\` |

装完之后程序本身在 `Program Files` 那类只读目录里，配置只能写用户目录 —— 这个分支在
`taffy_pet/paths.py` 里，是所有路径的唯一出口。**别的模块不要再自己写 `Path(__file__)`**，
打包之后那个路径是错的。

### 全部配置项

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `api_key` | `""` | DeepSeek 的 Key。环境变量 `DEEPSEEK_API_KEY` 优先级更高 |
| `height` | `200` | 角色高度，**逻辑像素**（会跟着系统缩放走，200% 缩放下 = 400 物理像素） |
| `opacity` | `1.0` | 整窗透明度 |
| `always_on_top` | `true` | 是否总在最前 |
| `blink` | `true` | 是否眨眼 |
| `speech` | `关注塔菲喵关注塔菲谢谢喵` | 点击时她说的话 |
| `volume` | `1.0` | 音量 |
| `pos` | `null` | 上次退出时的窗口位置，程序自己写 |
| `hint_shown` | `false` | 首次运行的操作提示弹过没有，程序自己写 |

环境变量优先是为了让人能临时换 Key 而不动文件。

`config.json` 在 `.gitignore` 里 —— 里面有 Key，**绝不能进版本库**。

## 重新生成素材

素材是从一张白底立绘离线生成的，原图不跟着仓库走（那是别人画的，得自己传进来）。
所以原图路径是命令行参数，不是写死的常量：

```bash
python tools/build_assets.py <白底立绘路径>
```

产出 `assets/taffy.png`（抠好的透明背景立绘）、`assets/taffy_blink.png`（闭眼帧）、
`assets/eyes.json`（眼睛坐标）。

音效同理，从录屏里截：

```bash
python tools/extract_audio.py <视频路径> click
```

抠图和闭眼帧的判据都踩过坑，改之前先看
[实现笔记](implementation-notes.md) —— 尤其闭眼帧那条，前三种做法看着都合理，全失败了。

## 自己打包安装程序

两步：PyInstaller 出绿色版目录，Inno Setup 把它包成安装向导。

```bash
# 1. 出 dist/TaffyPet/
python -m PyInstaller taffy-pet.spec

# 2. 包成安装程序（ISCC 路径按自己的安装位置改）
"C:\Users\<你>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\taffy-pet.iss
```

产物是 `installer/Output/TaffyPet-Setup-1.0.0.exe`。

`taffy-pet.spec` 用的是 **onedir 而不是 onefile**：onefile 每次启动都要把几十兆解压到临时
目录，桌宠是常驻程序，那次解压白等，而且解压目录会被杀软反复扫。onedir 启动快得多。

打包这条路上有三个坑，都**安安静静地失败、不报错**，只在实机跑一遍才看得出来
（静默安装永久卡死 / 卸载「成功」但一个文件没删 / 安装界面全是乱码）。
开改之前务必看 [实现笔记的打包那节](implementation-notes.md#打包成安装程序)。

## 自检

```bash
python tools/test_units.py    # 纯函数，不需要窗口：余额解析、动画曲线、配置读写
python tools/smoke.py         # 起窗口 → 截图 → 退出，顺便验弹跳不会溢出窗口
```

需要鼠标和网络的部分没法自动测：真实点击、右键菜单、DeepSeek 接口，那几处靠
`tools/smoke.py` 出图 + 手动点一遍。

`smoke.py` 输出的截图是 `assets/_*.png`，在 `.gitignore` 里 —— 那只是给人目检用的中间产物，
随时能重新生成。

## 项目结构

```
main.py                    入口：单实例保护、启动、崩溃时写日志
taffy_pet/
  paths.py                 所有路径的唯一出口（源码运行 vs 打包后差别全在这）
  pet.py                   窗口本体：无边框透明窗、点击/拖拽、右键菜单
  anim.py                  动画曲线：呼吸、弹跳关键帧、眨眼
  toast.py                 气泡
  balance.py               DeepSeek 余额查询（QThread，不卡界面）
  config.py                配置读写，API Key 的环境变量优先级
tools/                     一次性脚本，不进打包产物
  build_assets.py          白底立绘 → 透明背景立绘 + 闭眼帧
  extract_audio.py         录屏 → 音效
  make_shortcut.py         桌面快捷方式 + 图标
  smoke.py / test_units.py 自检
  _blink_explore.py 等     历史探针脚本，留着记录当时怎么试错的
installer/
  taffy-pet.iss            Inno Setup 脚本（必须存成带 BOM 的 UTF-8）
  languages/               官方中文语言包，原样存着别改
taffy-pet.spec             PyInstaller 配置
```

## 实现笔记

踩过的坑都在 [implementation-notes.md](implementation-notes.md)：抠图为什么不能用「到白色的距离」、
闭眼帧试了四版才找对、弹跳为什么放弃弹簧改用关键帧、高 DPI 屏上差点整只跑到屏幕外、
以及打包那三个不报错的坑。

## 版权

`assets/` 里的立绘是塔菲（第三方角色）的同人作品，版权不属于这个仓库，请勿用于商业用途；
音效取自一段录屏，同理。仓库目前没有附 LICENSE 文件。
