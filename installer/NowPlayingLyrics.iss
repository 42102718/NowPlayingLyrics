; Now Playing Lyrics 安裝程式 (Inno Setup 6)
; 編譯：installer\build-installer.bat，或用 Inno Setup 開啟本檔按 Compile
; 輸出：release\NowPlayingLyrics-Setup-<版本>.exe

#define MyAppName "Now Playing Lyrics"
#define MyAppVersion "1.4.1"
#define MyAppExe "Now Playing Lyrics.exe"
#define SrcDir "..\dist\Now Playing Lyrics"

[Setup]
; AppId 固定不變，之後的新版本才會覆蓋升級同一個程式
AppId={{AFE461CC-2DA0-4955-917E-6274BBD93C2B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoDescription={#MyAppName} Setup
; 裝在使用者自己的資料夾 (%LOCALAPPDATA%\Programs)，不需要系統管理員權限
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
DisableReadyPage=no
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExe}
SetupIconFile=..\icon.ico
WizardStyle=modern
WizardImageFile=wizard_100.bmp,wizard_125.bmp,wizard_150.bmp,wizard_175.bmp,wizard_200.bmp,wizard_250.bmp
WizardSmallImageFile=wizard_small_100.bmp,wizard_small_125.bmp,wizard_small_150.bmp,wizard_small_175.bmp,wizard_small_200.bmp,wizard_small_250.bmp
OutputDir=..\release
OutputBaseFilename=NowPlayingLyrics-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; 程式由下面的 PrepareToInstall 直接關閉，不跳「關閉應用程式」對話框
CloseApplications=no
ShowLanguageDialog=auto

[Languages]
Name: "cht"; MessagesFile: "ChineseTraditional.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
cht.OtherOptions=其他選項：
en.OtherOptions=Other options:
cht.AutoStart=開機時自動啟動（只在背景顯示浮動歌詞和系統匣圖示）
en.AutoStart=Start automatically when I sign in (floating lyrics + tray icon only)
cht.PathTooLong=這個安裝路徑太長了，程式可能無法啟動。%n%n請選一個比較短的資料夾，例如：%n%1
en.PathTooLong=This folder path is too long and the app may fail to start.%n%nPlease choose a shorter folder, for example:%n%1

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "{cm:AutoStart}"; GroupDescription: "{cm:OtherOptions}"; Flags: unchecked

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\使用說明.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; 與程式內「開機時自動啟動」開關用同一個值，兩邊會同步
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "NowPlayingLyrics"; ValueData: """{app}\{#MyAppExe}"" --background"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 先關掉執行中的程式，並移除程式內自行設定的開機啟動
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM ""{#MyAppExe}"""; Flags: runhidden; RunOnceId: "KillApp"
Filename: "{sys}\reg.exe"; Parameters: "delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v NowPlayingLyrics /f"; Flags: runhidden; RunOnceId: "DelRunKey"

[UninstallDelete]
; 使用者設定、字體快取、WebView 快取
Type: filesandordirs; Name: "{userappdata}\NowPlayingLyrics"
; AI 模型與 GPU 元件 (第一次用 AI 時下載)
Type: filesandordirs; Name: "{localappdata}\NowPlayingLyrics"
Type: filesandordirs; Name: "{app}"

[Code]
const
  MaxDirLen = 150;  { 程式內最深的檔案還有約 92 字元，總長要低於 Windows 的 260 限制 }

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  R: Integer;
begin
  { 升級時先關掉正在執行的舊版 }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "{#MyAppExe}"', '', SW_HIDE, ewWaitUntilTerminated, R);
  Sleep(800);
  Result := '';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and (Length(WizardDirValue) > MaxDirLen) then
  begin
    MsgBox(FmtMessage(CustomMessage('PathTooLong'), [ExpandConstant('{autopf}\{#MyAppName}')]), mbError, MB_OK);
    Result := False;
  end;
end;
