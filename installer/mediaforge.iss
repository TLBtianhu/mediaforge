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
DisableDirPage=no
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
UninstallDisplayIcon={app}\app\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
DirExistsWarning=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "dshbridge"; Description: "附带 DSH 集成桥（由 DSH 插件管理器加载）"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\{#MyAppExeName}"; DestDir: "{app}\app"; Flags: ignoreversion
Source: "{#SourceDir}\*"; DestDir: "{app}\app"; Excludes: "python-runtime\runtime-envs\*,integrations\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\integrations\*"; DestDir: "{app}\integrations"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Tasks: dshbridge
; The bootstrap runtime stays read-only; only runtime-envs is writable for managed installs.

[Dirs]
Name: "{app}\data"
Name: "{app}\models"
Name: "{app}\runtime-envs"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\app\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\app\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; 安装路径指针：MCP 启动器（%USERPROFILE%\.mediaforge\bin\mediaforge-mcp.cmd）按
; MEDIAFORGE_HOME → 这个键 → %LOCALAPPDATA%\Programs → Program Files 的顺序找应用。
; 写在声明区而不是脚本里，脚本失败也不会丢指针。
Root: HKCU; Subkey: "Software\MediaForge"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}\app"; Flags: uninsdeletekey

[Run]
; DSH 集成只随包提供能力，不由安装器修改宿主配置；以后由 DSH 插件管理器加载。
Filename: "{app}\app\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
function DataRoot(): string;
var
  EnvRoot: string;
begin
  { 与应用 storage::data_root_dir 对齐：MEDIAFORGE_DATA_ROOT 优先，否则用户目录。
    升级修复必须写进真实数据根，不能在自定义数据根时误写 ~/.mediaforge。 }
  EnvRoot := GetEnv('MEDIAFORGE_DATA_ROOT');
  if EnvRoot <> '' then
    Result := EnvRoot
  else
    Result := ExpandConstant('{app}\data');
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
  RemoveIfExists(ExpandConstant('{app}\app') + '\.git');
  RemoveIfExists(ExpandConstant('{app}\app') + '\.github');
  RemoveIfExists(ExpandConstant('{app}\app') + '\.claude');
  RemoveIfExists(ExpandConstant('{app}\app') + '\.omc');
  RemoveIfExists(ExpandConstant('{app}\app') + '\.spec-workflow');
  RemoveIfExists(ExpandConstant('{app}\app') + '\__pycache__');
  RemoveIfExists(ExpandConstant('{app}\app') + '\python-runtime\Doc');
  RemoveIfExists(ExpandConstant('{app}\app') + '\python-runtime\include');
  RemoveIfExists(ExpandConstant('{app}\app') + '\python-runtime\libs');
end;

procedure RepairVenvConfig(Backend: string);
var
  EnvDir: string;
  ConfigPath: string;
  PythonRuntimeDir: string;
  PythonExe: string;
  Content: string;
begin
  EnvDir := DataRoot() + '\runtime-envs\' + Backend;
  ConfigPath := EnvDir + '\pyvenv.cfg';
  PythonRuntimeDir := ExpandConstant('{app}\app') + '\python-runtime';
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


procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RepairBundledRuntimeEnvs();
    CleanupInstallTree();
      SaveStringToFile(ExpandConstant('{app}') + '\mediaforge.inno-install', 'managed' + #13#10, False);
  end;
end;

var
  CleanupOptions: TNewCheckListBox;
  SelectAllButton: TNewButton;

procedure SelectAllCleanup(Sender: TObject);
var
  Index: Integer;
begin
  for Index := 0 to CleanupOptions.Items.Count - 1 do
    CleanupOptions.Checked[Index] := True;
end;

procedure InitializeUninstallProgressForm();
begin
  if UninstallSilent then
    Exit;
  CleanupOptions := TNewCheckListBox.Create(UninstallProgressForm);
  CleanupOptions.Parent := UninstallProgressForm;
  CleanupOptions.Left := 0;
  CleanupOptions.Top := 80;
  CleanupOptions.Width := UninstallProgressForm.ClientWidth;
  CleanupOptions.Height := 120;
  CleanupOptions.Items.Add('同时删除独立运行时（runtime-envs）');
  CleanupOptions.Items.Add('同时删除模型资源（models）');
  CleanupOptions.Items.Add('同时删除配置、日志和任务历史（data）');

  SelectAllButton := TNewButton.Create(UninstallProgressForm);
  SelectAllButton.Parent := UninstallProgressForm;
  SelectAllButton.Left := 0;
  SelectAllButton.Top := 205;
  SelectAllButton.Width := 120;
  SelectAllButton.Caption := '全选以上资源';
  SelectAllButton.OnClick := @SelectAllCleanup;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if Assigned(CleanupOptions) then
    begin
      if CleanupOptions.Checked[0] then RemoveIfExists(ExpandConstant('{app}\runtime-envs'));
      if CleanupOptions.Checked[1] then RemoveIfExists(ExpandConstant('{app}\models'));
      if CleanupOptions.Checked[2] then RemoveIfExists(ExpandConstant('{app}\data'));
    end;
  end;
end;
