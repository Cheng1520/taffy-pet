; 塔菲桌宠 —— Inno Setup 安装脚本
;
; 编译（ISCC 的路径按实际安装位置改）：
;     "C:\Users\<你>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\taffy-pet.iss
;
; 前置条件：先跑过 PyInstaller，dist\TaffyPet\ 得是齐的：
;     python -m PyInstaller taffy-pet.spec
;
; 这个文件必须存成「带 BOM 的 UTF-8」。Inno 6 靠 BOM 判断编码，没有 BOM 就按系统
; ANSI 码页解 —— 在中文 Windows 上那些中文界面文字会全变乱码。

#define AppName "塔菲桌宠"
#define AppVersion "1.0.0"
#define AppExe "TaffyPet.exe"
#define RepoURL "https://github.com/Cheng1520/taffy-pet"

[Setup]
; AppId 是卸载信息的身份，一旦发出去就永远不能再改 —— 改了之后旧版本卸载不掉、
; 新版本会被当成另一个软件并排装两份。
AppId={{32E65C63-575F-4F3B-8316-F56A53CDBE46}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Cheng1520
AppPublisherURL={#RepoURL}
AppSupportURL={#RepoURL}/issues
AppComments=非官方粉丝作品，立绘来自第三方（见仓库 README）

; 装成「只为我安装」：{autopf} 在 lowest 权限下解析成
; %LOCALAPPDATA%\Programs\TaffyPet，全程不弹 UAC。桌面小玩具没必要强制管理员，
; 而且非管理员账户根本走不了全机安装那条路，给个选不了的选项只是添乱。
DefaultDirName={autopf}\TaffyPet
PrivilegesRequired=lowest

; 这里刻意只写 commandline，**不能写 dialog**。
; 写 dialog 的话 Inno 会在启动时先弹一个「选择安装模式」框，而且这个框在
; /VERYSILENT 下照样弹 —— 实测：静默安装会一声不响地永久卡在那个框上，日志都
; 不写一行，看起来就像安装程序坏了。留 commandline 是为了还想全机安装的人可以
; 自己传 /ALLUSERS，但默认路径上不打扰任何人。
PrivilegesRequiredOverridesAllowed=commandline
DisableProgramGroupPage=yes
AllowNoIcons=yes

; 64 位 PyInstaller 产物，装到 32 位目录里会起不来
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

OutputDir=Output
OutputBaseFilename=TaffyPet-Setup-{#AppVersion}
SetupIconFile=..\assets\taffy.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} 安装程序
VersionInfoProductName={#AppName}

[Languages]
; Inno 官方只带英法德日那几个，中文得自己带 —— languages\ChineseSimplified.isl 是从
; jrsoftware/issrc 的 Files\Languages\ 取来的官方译文（6.5.0+ 版），原样存着别改。
;
; 只挂中文一门：这个程序和它的 README 本来就全是中文的，多挂一门英文只会让每个人都
; 先挨一次「选语言」的弹窗，换不到任何东西。
Name: "chinese"; MessagesFile: "{#SourcePath}\languages\ChineseSimplified.isl"

[Tasks]
; 不写 GroupDescription：Inno 在「准备安装」摘要页上自己会打一行「附加任务:」，
; 再给任务指定分组标题就会变成「附加任务: / 附加任务: / 创建桌面快捷方式」，
; 重复一行。只有一个任务，也用不着分组。
; 默认勾上 —— 桌宠本来就该在桌面上。
Name: "desktopicon"; Description: "创建桌面快捷方式"

[Files]
; 整个 dist\TaffyPet\ 原样搬过去，别挑文件 —— PyInstaller 的 _internal\ 里
; 少一个 DLL 就是启动即崩，而且崩得没头没尾。
Source: "..\dist\TaffyPet\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 语音库和跳舞精灵表**不进版本库**（见仓库 .gitignore 的 /voice/ 和 /dance/），
; 但必须进安装包 —— 要的就是「GitHub 上发出去的安装包装完和本机一致」。
; 仓库里只留读它们的代码（taffy_pet\voice.py、pet.py）和装它们的脚本。
;
; 目的地是 {userappdata}，也就是 %APPDATA%\TaffyPet\ —— 桌宠运行时从那儿读
; （paths.py 的 VOICE_DIR / DANCE_DIR）。**不能放进 {app}**：打包之后 DATA_DIR
; 固定指 %APPDATA%\TaffyPet，放安装目录里她一个字节都不会去找。
;
; 这两个目录是构建前手工准备好的（tools 里那两个 taffy_install_*.py 负责生成并
; 同时装到本机两份）。缺了它们 ISCC 会直接报错停下 —— 那是好事，比打出个
; 装完没声音的包发出去强。
Source: "..\voice\*"; DestDir: "{userappdata}\TaffyPet\voice"; Flags: ignoreversion
Source: "..\dance\*"; DestDir: "{userappdata}\TaffyPet\dance"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Description: "立刻把塔菲叫出来"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

; 卸载**绝不能**把 %APPDATA%\TaffyPet 整个删掉 —— 那里面有用户填的 API Key 和
; 聊天记录。重装一次还要重新贴 Key 是最招人烦的失败方式。想彻底清干净就手动删。
;
; 语音库和精灵表现在也装在那儿（见 [Files]），它们会被卸载器带走 —— 那是
; Inno 按自己的安装清单删的，只删它装进去的那几个文件，config.json / chat.json /
; memory.md 一个都不碰。**别为了「顺手清干净」把 {userappdata}\TaffyPet 加进
; [UninstallDelete]**：那条按目录删，会把 API Key 一起带走。

[UninstallDelete]
; 程序自己可能往安装目录里落东西（老版本的 config.json / taffy.log 就走这儿），
; 卸载器只认自己装进去的文件，剩下的得点名删。
Type: filesandordirs; Name: "{app}"


[Code]
{ 装或卸之前先把她关掉。
  ----------
  非管理员模式安装时，Inno 不做 Restart Manager 那套「找出占用文件的进程并关掉」
  的动作（日志里会写 Administrative install mode: No，CloseApplications 直接失效）。
  实测的后果：塔菲还开着的时候卸载，卸载器会「成功退出、文件一个没删」—— 注册表项
  和快捷方式都清干净了，程序目录却原封不动留着，用户只看到「卸载了但还在」。

  先温和地送 WM_CLOSE（taskkill 不带 /F），给她机会把窗口位置存进 config.json；
  等一下还在，再强杀。 }
procedure StopRunningPet();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'),
       '/IM {#AppExe} /T', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1200);
  Exec(ExpandConstant('{sys}\taskkill.exe'),
       '/IM {#AppExe} /F /T', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function InitializeSetup(): Boolean;
begin
  StopRunningPet();
  Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningPet();
  Result := True;
end;
