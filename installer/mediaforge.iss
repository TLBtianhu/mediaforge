; MediaForge Windows CUDA installer.
; The source directory is prepared by GitHub Actions and passed through MEDIAFORGE_PORTABLE_DIR.

#define MyAppName "MediaForge"
#define MyAppVersion GetEnv("MEDIAFORGE_VERSION") == "" ? "0.0.1" : GetEnv("MEDIAFORGE_VERSION")
#define MyAppPublisher "TheSmallHanCat"
#define MyAppExeName "MediaForge.exe"
#define SourceDir GetEnv("MEDIAFORGE_PORTABLE_DIR")
#define OutputDir GetEnv("MEDIAFORGE_INSTALLER_OUTPUT") == "" ? "..\release" : GetEnv("MEDIAFORGE_INSTALLER_OUTPUT")
#define PackageSuffix GetEnv("MEDIAFORGE_PACKAGE_SUFFIX") == "" ? "windows-x64-cuda" : GetEnv("MEDIAFORGE_PACKAGE_SUFFIX")
#define InstallerIcon GetEnv("MEDIAFORGE_INSTALLER_ICON") == "" ? "..\src-tauri\icons\icon.ico" : GetEnv("MEDIAFORGE_INSTALLER_ICON")

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
; Default remains stable for upgrades; /DIR allows isolated candidate validation.
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
; Dictionary is in KiB. Bound compression memory on developer/CI machines.
LZMADictionarySize=65536
LZMANumBlockThreads=2
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#InstallerIcon}
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

[InstallDelete]
; Older builds shipped the developer-only PS1 helpers into the user's install.
; Delete them on upgrade so nobody can run the legacy injector by habit.
Type: files; Name: "{app}\integrations\dsh-mediaforge\*.ps1"
Type: files; Name: "{app}\integrations\dsh-mediaforge\*.bak*"
Type: files; Name: "{app}\integrations\dsh-mediaforge\launcher.template.cmd"
Type: files; Name: "{app}\integrations\dsh-mediaforge\mediaforge-mcp.cmd"

[Files]
Source: "{#SourceDir}\{#MyAppExeName}"; DestDir: "{app}\app"; Flags: ignoreversion
; Root anchors preserve code directories such as pip/_internal/models and distlib/data.
Source: "{#SourceDir}\*"; DestDir: "{app}\app"; Excludes: "\{#MyAppExeName},\mediaforge.portable,\python-runtime\runtime-envs\*,\integrations\*,\models\*,\data\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\integrations\*"; DestDir: "{app}\integrations"; Excludes: "*.ps1,*.bak*"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Tasks: dshbridge
; The bootstrap runtime stays read-only; only runtime-envs is writable for managed installs.

[Dirs]
Name: "{app}\data"
Name: "{app}\models"
Name: "{app}\runtime-envs"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\app\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\app\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; 记录当前安装程序的位置。DSH 按用户链接的 bundle 解析所在实例。
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

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    CleanupInstallTree();
      SaveStringToFile(ExpandConstant('{app}') + '\mediaforge.inno-install', 'managed' + #13#10, False);
  end;
end;

var
  RuntimeCheck: TNewCheckBox;
  ModelsCheck: TNewCheckBox;
  DataCheck: TNewCheckBox;
  SelectAllButton: TNewButton;
  RemoveRuntime: Boolean;
  RemoveModels: Boolean;
  RemoveData: Boolean;

procedure SelectAllCleanup(Sender: TObject);
begin
  RuntimeCheck.Checked := True;
  ModelsCheck.Checked := True;
  DataCheck.Checked := True;
end;

function ChooseCleanupOptions(): Boolean;
var
  Form: TSetupForm;
  OkButton: TNewButton;
  CancelButton: TNewButton;
begin
  RemoveRuntime := False;
  RemoveModels := False;
  RemoveData := False;
  Form := CreateCustomForm(ScaleX(460), ScaleY(220), False, True);
  try
    Form.Caption := '选择卸载内容';
    Form.ClientWidth := 460;
    Form.ClientHeight := 220;

    RuntimeCheck := TNewCheckBox.Create(Form);
    RuntimeCheck.Parent := Form;
    RuntimeCheck.Left := 16;
    RuntimeCheck.Top := 16;
    RuntimeCheck.Width := 420;
    RuntimeCheck.Caption := '同时删除独立运行时（runtime-envs）';
    RuntimeCheck.Checked := False;

    ModelsCheck := TNewCheckBox.Create(Form);
    ModelsCheck.Parent := Form;
    ModelsCheck.Left := 16;
    ModelsCheck.Top := 44;
    ModelsCheck.Width := 420;
    ModelsCheck.Caption := '同时删除模型资源（models）';
    ModelsCheck.Checked := False;

    DataCheck := TNewCheckBox.Create(Form);
    DataCheck.Parent := Form;
    DataCheck.Left := 16;
    DataCheck.Top := 72;
    DataCheck.Width := 420;
    DataCheck.Caption := '同时删除配置、日志和任务历史（data）';
    DataCheck.Checked := False;

    SelectAllButton := TNewButton.Create(Form);
    SelectAllButton.Parent := Form;
    SelectAllButton.Left := 16;
    SelectAllButton.Top := 108;
    SelectAllButton.Width := 150;
    SelectAllButton.Caption := '全选以上资源';
    SelectAllButton.OnClick := @SelectAllCleanup;

    OkButton := TNewButton.Create(Form);
    OkButton.Parent := Form;
    OkButton.Left := 238;
    OkButton.Top := 165;
    OkButton.Width := 90;
    OkButton.Caption := '开始卸载';
    OkButton.ModalResult := mrOk;
    OkButton.Default := True;

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.Left := 338;
    CancelButton.Top := 165;
    CancelButton.Width := 90;
    CancelButton.Caption := '取消';
    CancelButton.ModalResult := mrCancel;
    CancelButton.Cancel := True;

    Result := Form.ShowModal() = mrOk;
    if Result then
    begin
      RemoveRuntime := RuntimeCheck.Checked;
      RemoveModels := ModelsCheck.Checked;
      RemoveData := DataCheck.Checked;
    end;
  finally
    Form.Free;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  if UninstallSilent then
  begin
    Result := True;
    Exit;
  end;
  Result := ChooseCleanupOptions();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if RemoveRuntime then RemoveIfExists(ExpandConstant('{app}\runtime-envs'));
    if RemoveModels then RemoveIfExists(ExpandConstant('{app}\models'));
    if RemoveData then RemoveIfExists(ExpandConstant('{app}\data'));
  end;
end;
