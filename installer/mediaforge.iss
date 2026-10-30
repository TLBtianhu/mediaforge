; MediaForge Windows CUDA installer.
; The source directory is prepared by GitHub Actions and passed through MEDIAFORGE_PORTABLE_DIR.

#define MyAppName "MediaForge"
#define MyAppVersion GetEnv("MEDIAFORGE_VERSION") == "" ? "0.0.1" : GetEnv("MEDIAFORGE_VERSION")
#define MyAppPublisher "TheSmallHanCat"
#define MyAppExeName "MediaForge.exe"
#define SourceDir GetEnv("MEDIAFORGE_PORTABLE_DIR")
#define OutputDir GetEnv("MEDIAFORGE_INSTALLER_OUTPUT") == "" ? "..\release" : GetEnv("MEDIAFORGE_INSTALLER_OUTPUT")
#define PackageSuffix GetEnv("MEDIAFORGE_PACKAGE_SUFFIX") == "" ? "windows-x64-cuda" : GetEnv("MEDIAFORGE_PACKAGE_SUFFIX")

#if SourceDir == ""
  #error MEDIAFORGE_PORTABLE_DIR is required. It must point to the staged portable directory.
#endif

[Setup]
PrivilegesRequired=lowest
; Unique AppId for MediaForge. Do not reuse AppIds from other projects. Keep it stable or in-place upgrades break.
AppId={{34A687CC-EE1D-401D-8FE1-98E4061C32CB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={localappdata}\Programs\{#MyAppName}
; 用户不选目录：安装位置是常量，升级自然落在同一处。
DisableDirPage=yes
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Select the installer process architecture independently of the install mode.
SetupArchitecture=x64
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir={#OutputDir}
OutputBaseFilename=MediaForge-{#MyAppVersion}-{#PackageSuffix}-setup
Compression=lzma2/max
LZMAUseSeparateProcess=yes
LZMADictionarySize=1048576
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\src-tauri\icons\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
DirExistsWarning=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "python-runtime\runtime-envs\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\python-runtime\runtime-envs\*"; DestDir: "{userprofile}\.mediaforge\runtime-envs"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: not DirExists(ExpandConstant('{userprofile}\.mediaforge\runtime-envs\cuda')) Excludes: "active-runtime.json"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Check: ShouldInstallBundledRuntimeEnvs
; The bootstrap runtime stays read-only; only runtime-envs is writable for managed installs.

[Dirs]
; Keep the bootstrap interpreter read-only. Only managed environments need user write access.
Name: "{userprofile}\.mediaforge\runtime-envs"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安装结束把 MediaForge 接进 DeepSeek Harness（没有 DSH 时脚本自己跳过）。
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\integrations\dsh-mediaforge\register.ps1"""; StatusMsg: "Registering MediaForge with DeepSeek Harness..."; Flags: runhidden waituntilterminated skipifdoesntexist
Filename: "{app}\bin\VC_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing Microsoft Visual C++ Runtime..."; Flags: waituntilterminated runhidden skipifdoesntexist; Check: IsAdminInstallMode and not IsVCRedistX64Installed
Filename: "{cmd}"; Parameters: "/C del /F /Q ""{app}\bin\VC_redist.x64.exe"""; Flags: runhidden waituntilterminated skipifdoesntexist; Check: IsAdminInstallMode and not IsVCRedistX64Installed
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  FreshInstallKnown: Boolean;
  FreshInstallResult: Boolean;

function IsVCRedistX64Installed(): Boolean;
var
  Installed: Cardinal;
begin
  Result :=
    RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed)
    and (Installed = 1);
end;

function IsFreshInstall(): Boolean;
begin
  Result := not FileExists(ExpandConstant('{app}\mediaforge.inno-install'));
end;

function CachedFreshInstall(): Boolean;
begin
  if not FreshInstallKnown then
  begin
    FreshInstallResult := IsFreshInstall();
    FreshInstallKnown := True;
  end;
  Result := FreshInstallResult;
end;

function BundledBackend(): string;
begin
  if Pos('cpu', LowerCase('{#PackageSuffix}')) > 0 then
    Result := 'cpu'
  else if Pos('cuda', LowerCase('{#PackageSuffix}')) > 0 then
    Result := 'cuda'
  else if Pos('rocm', LowerCase('{#PackageSuffix}')) > 0 then
    Result := 'rocm'
  else
    Result := '';
end;

function ExistingBundledBackendPython(): Boolean;
var
  Backend: string;
begin
  Backend := BundledBackend();
  if Backend = '' then
  begin
    Result := False;
    Exit;
  end;
  Result := FileExists(
    ExpandConstant('{userprofile}\.mediaforge\runtime-envs\') + Backend + '\Scripts\python.exe'
  );
end;

function ShouldInstallBundledRuntimeEnvs(): Boolean;
begin
  { Fresh installs and upgrades from layouts without the current backend receive the package env.
    A valid current backend is preserved so an upgrade does not replace a user-managed env. }
  Result := CachedFreshInstall() or not ExistingBundledBackendPython();
end;

procedure RemoveIfExists(Path: string);
begin
  if DirExists(Path) then
  begin
    DelTree(Path, True, True, True);
  end
  else if FileExists(Path) then
  begin
    DeleteFile(Path);
  end;
end;

procedure CleanupInstallTree();
begin
  { Remove known development leftovers if they ever slip into the staged directory. }
  RemoveIfExists(ExpandConstant('{app}') + '\.git');
  RemoveIfExists(ExpandConstant('{app}') + '\.github');
  RemoveIfExists(ExpandConstant('{app}') + '\.claude');
  RemoveIfExists(ExpandConstant('{app}') + '\.omc');
  RemoveIfExists(ExpandConstant('{app}') + '\.spec-workflow');
  RemoveIfExists(ExpandConstant('{app}') + '\__pycache__');
  RemoveIfExists(ExpandConstant('{app}') + '\python-runtime\Doc');
  RemoveIfExists(ExpandConstant('{app}') + '\python-runtime\include');
  RemoveIfExists(ExpandConstant('{app}') + '\python-runtime\libs');
end;

procedure RepairVenvConfig(Backend: string);
var
  EnvDir: string;
  ConfigPath: string;
  PythonRuntimeDir: string;
  PythonExe: string;
  Content: string;
begin
  EnvDir := ExpandConstant('{userprofile}\.mediaforge\runtime-envs\') + Backend;
  ConfigPath := EnvDir + '\pyvenv.cfg';
  PythonRuntimeDir := ExpandConstant('{app}') + '\python-runtime';
  PythonExe := PythonRuntimeDir + '\python.exe';

  if (not FileExists(ConfigPath)) or (not FileExists(PythonExe)) then
  begin
    Exit;
  end;

  Content :=
    'home = ' + PythonRuntimeDir + #13#10 +
    'include-system-site-packages = false' + #13#10 +
    'executable = ' + PythonExe + #13#10 +
    'command = ' + PythonExe + ' -m venv ' + EnvDir + #13#10;
  if not SaveStringToFile(ConfigPath, Content, False) then
  begin
    RaiseException('Failed to write Python venv config: ' + ConfigPath);
  end;

  if Backend = 'rocm' then
  begin
    { ROCm's pip console launcher embeds the CI Python path and is not relocatable. }
    RemoveIfExists(EnvDir + '\Scripts\offload-arch.exe');
  end;
end;

procedure RepairBundledRuntimeEnvs();
begin
  RepairVenvConfig('cpu');
  RepairVenvConfig('cuda');
  RepairVenvConfig('rocm');
end;

// 把 MediaForge 的 MCP 启动器写到用户级固定位置。
// DSH 插件包（integrations/dsh-mediaforge）只引用这个固定路径，
// 所以包里不需要任何机器相关的绝对路径。
procedure WriteMcpLauncher();
var
  Dir, Target, Content: String;
begin
  Dir := ExpandConstant('{localappdata}\MediaForge\bin');
  if not ForceDirectories(Dir) then
    Exit;
  Target := Dir + '\mediaforge-mcp.cmd';
  Content :=
    '@echo off' + #13#10 +
    'rem Generated by the MediaForge installer. Do not edit the path by hand.' + #13#10 +
    'setlocal' + #13#10 +
    'set "MF_ROOT=' + ExpandConstant('{app}') + '"' + #13#10 +
    'if not exist "%MF_ROOT%\python\agent_bridge.py" (' + #13#10 +
    '  echo MediaForge MCP launcher: agent_bridge.py not found under "%MF_ROOT%" 1>&2' + #13#10 +
    '  exit /b 2' + #13#10 +
    ')' + #13#10 +
    'if defined MEDIAFORGE_PYTHON (' + #13#10 +
    '  "%MEDIAFORGE_PYTHON%" "%MF_ROOT%\python\agent_bridge.py" --stdio' + #13#10 +
    '  exit /b %ERRORLEVEL%' + #13#10 +
    ')' + #13#10 +
    'if exist "%MF_ROOT%\python-runtime\python.exe" (' + #13#10 +
    '  "%MF_ROOT%\python-runtime\python.exe" "%MF_ROOT%\python\agent_bridge.py" --stdio' + #13#10 +
    '  exit /b %ERRORLEVEL%' + #13#10 +
    ')' + #13#10 +
    'where py >nul 2>nul' + #13#10 +
    'if not errorlevel 1 (' + #13#10 +
    '  py -3 "%MF_ROOT%\python\agent_bridge.py" --stdio' + #13#10 +
    '  exit /b %ERRORLEVEL%' + #13#10 +
    ')' + #13#10 +
    'python "%MF_ROOT%\python\agent_bridge.py" --stdio' + #13#10 +
    'exit /b %ERRORLEVEL%' + #13#10;
  SaveStringToFile(Target, Content, False);

  RegWriteStringValue(HKCU, 'Software\\MediaForge', 'InstallDir', ExpandConstant('{app}'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    FreshInstallResult := IsFreshInstall();
    FreshInstallKnown := True;
  end;
  if CurStep = ssPostInstall then
  begin
    RepairBundledRuntimeEnvs();
    CleanupInstallTree();
    WriteMcpLauncher();
    SaveStringToFile(ExpandConstant('{app}') + '\mediaforge.inno-install', 'managed' + #13#10, False);
  end;
end;
