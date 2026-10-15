; Manna 的 Windows 安装包（Inno Setup 6）。由 build_windows.ps1 调用：
;   ISCC /DAppVersion=1.1.0 /DSourceDir=...\dist\Manna /DIconFile=...\icon.ico /DOutDir=...\dist Manna.iss
; 装到当前用户自己的目录（不需要管理员权限），开始菜单里有 Manna，可选桌面快捷方式。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E2B7C1A-4F0D-4E8B-9C55-3A1D2F7B8E90}
AppName=Manna
AppVersion={#AppVersion}
AppVerName=Manna {#AppVersion}
AppPublisher=Manna
DefaultDirName={autopf}\Manna
DefaultGroupName=Manna
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=Manna-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Manna.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "zh"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#endif
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Manna"; Filename: "{app}\Manna.exe"
Name: "{group}\{cm:UninstallProgram,Manna}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Manna"; Filename: "{app}\Manna.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Manna.exe"; Description: "{cm:LaunchProgram,Manna}"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; 升级时清掉旧版本的程序文件（下载的视频和设置在别处，不受影响）
Type: filesandordirs; Name: "{app}\_internal"

[Code]
// Manna 的窗口用系统的 WebView2 显示。Windows 11 自带；少数 Windows 10 没有，提示去微软官网装
function HasWebView2(): Boolean;
var
  v: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  code: Integer;
begin
  if (CurStep = ssPostInstall) and (not WizardSilent) and (not HasWebView2()) then
    if MsgBox('这台电脑没有安装 Microsoft Edge WebView2 运行库，Manna 的窗口需要它才能显示。' + #13#10 +
              '点「是」打开微软官网下载（选 Evergreen Bootstrapper，安装后再打开 Manna）。',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, code);
end;
