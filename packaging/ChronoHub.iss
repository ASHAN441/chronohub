; 时枢 ChronoHub 安装脚本（Inno Setup 6）
;
; 编译：python tools/build_installer.py
;       或 ISCC.exe /DAppVersion=3.5.1 packaging\ChronoHub.iss
;
; 几点刻意为之的设计：
;
; 1) 装到 {localappdata}\Programs\ChronoHub，不是 Program Files。
;    程序把数据写在 exe 旁边的 data\ 里（便携式布局），装到 Program Files
;    普通用户没有写权限，程序会存不了任何数据。装到用户目录既免 UAC，
;    data\ 也能正常写。
;
; 2) 打包时**排除 data\**。安装包里不能带开发机上的课表、账号库；
;    覆盖安装时也不会碰已有的 data\（它不在任何安装清单里）。
;
; 3) 卸载时**默认保留 data\**，只有用户明确点「是」才删。

#define AppName        "时枢 ChronoHub"
#define AppShortName   "ChronoHub"
#define AppPublisher   "SYNUTV"
#define AppURL         "https://github.com/ASHAN441/chronohub"
#define AppExeName     "ChronoHub.exe"

; 版本号由 tools/build_installer.py 从 app/config.py 读出来传进来，
; 避免两处手改不同步。单独跑 ISCC 时用这里的兜底值。
#ifndef AppVersion
  #define AppVersion "3.5.1"
#endif

[Setup]
; AppId 一旦发布就不能改，否则新版本会被当成另一个软件（覆盖安装失效）
AppId={{9E1FC794-DB3A-4399-A813-C0DE518BE8DE}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; 用户目录安装：不需要管理员权限，data\ 可写
DefaultDirName={localappdata}\Programs\{#AppShortName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

OutputDir=..\dist\installer
OutputBaseFilename=ChronoHub-Setup-v{#AppVersion}
SetupIconFile=ChronoHub.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}

Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
; 安装/升级前自动关掉正在运行的程序（否则文件被占用会装失败）
CloseApplications=yes
RestartApplications=no
; 升级时保留用户改过的配置类文件（我们只有 data\，不在清单里，天然保留）
UsePreviousAppDir=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
; 主程序：dist\ChronoHub 整目录，但排除 data\
Source: "..\dist\ChronoHub\*"; DestDir: "{app}"; \
    Excludes: "data\*,data"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; 附一份使用说明到安装目录，方便找不到文档的人
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即运行 {#AppName}"; \
    Flags: nowait postinstall skipifsilent

; 刻意不写 [UninstallDelete] 来删 data\ —— 用户数据默认保留，
; 要不要删交给下面 CurUninstallStepChanged 里问用户。

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{app}\data');
    if DirExists(DataDir) then
    begin
      if MsgBox('是否同时删除你的数据？' + #13#10 + #13#10 +
                'data 目录里有课表、教务账号、值班表、导出的文件。' + #13#10 +
                '选「是」会全部删除且无法恢复；' + #13#10 +
                '选「否」则保留，以后重装这个软件还能继续用。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
