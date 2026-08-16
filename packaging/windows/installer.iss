; Unscreen installer - Inno Setup script.
;
; Compile (from the repository root):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" ^
;     /DMyAppVersion=0.5.0 /DMyAppExeName=unscreen.exe /DBundleDir=build\bundle ^
;     packaging\windows\installer.iss
;
; Flows, decided in InitializeSetup from the registry state:
;   * install    - nothing found               -> full wizard
;   * upgrade    - older version installed     -> silent uninstall before
;                  copying (user data in %APPDATA% is untouched), minimal
;                  wizard with "updated from" wording
;   * repair     - same version installed      -> maintenance page offering
;                                                 Modify / Repair / Remove
;   * downgrade  - newer version installed      -> abort with a hint
;   * maintenance - started with /maintenance  -> maintenance page (wired up
;                                                 from Programs & Features)
;
; Programs & Features: a copy of this installer is placed in {app} and wired
; via AppModifyPath, so the "Change" button opens the maintenance page.

#define MyAppName "Unscreen"
#define MyAppId "D2E3F4A5-B6C7-48D9-A0B1-C2D3E4F5A6B7"
#ifndef MyAppPublisher
  #define MyAppPublisher "Sakth1"
#endif
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
; VersionInfo and version comparisons only accept numeric dotted versions;
; derive one from the semver (e.g. "0.4.5-dev" -> "0.4.5").
#if Pos("-", MyAppVersion) > 0
  #define MyAppVersionNumeric Copy(MyAppVersion, 1, Pos("-", MyAppVersion) - 1)
#else
  #define MyAppVersionNumeric MyAppVersion
#endif
#ifndef MyAppExeName
  #define MyAppExeName "unscreen.exe"
#endif
#ifndef BundleDir
  #define BundleDir "build\bundle"
#endif
#ifndef MyAppComments
  #define MyAppComments "Cross-device app usage timeline tracker"
#endif
#define MyAppReleaseUrl "https://github.com/sakth1/Unscreen/releases/latest"

[Setup]
; IMPORTANT: AppId must never change, it is what lets the installer detect
; existing installations (registry key "{#MyAppId}_is1").
AppId={{D2E3F4A5-B6C7-48D9-A0B1-C2D3E4F5A6B7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments={#MyAppComments}
AppSupportURL={#MyAppReleaseUrl}
AppUpdatesURL={#MyAppReleaseUrl}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
WizardResizable=yes
WizardImageFile=assets\wizard-welcome.bmp
WizardSmallImageFile=assets\wizard-small.bmp
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
; The app holds the named mutex "Unscreen_Mutex" while running; setup will
; wait for/close it instead of failing when the app is open during an update.
AppMutex=Unscreen_Mutex
; Offers "Install for anyone who uses this computer / Only for me".
PrivilegesRequiredOverridesAllowed=dialog
; Lets the Settings app (Programs and Features / 8.3) show a working Modify
; button that starts the maintenance page.
AppModifyPath={app}\Unscreen-Setup.exe /maintenance
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\..\src\assets\icon_windows.ico
VersionInfoVersion={#MyAppVersionNumeric}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersionNumeric}
VersionInfoDescription={#MyAppComments}
VersionInfoCompany={#MyAppPublisher}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Hides the "Dir already exists" prompt when repairing an install.
EnableDirDoesntExistWarning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Run Unscreen when Windows starts"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autoupdate"; Description: "Check for updates automatically"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

; Files produced at install time that the uninstaller does not know about.
[UninstallDelete]
Type: files; Name: "{app}\setup-flags.ini"
Type: files; Name: "{app}\Unscreen-Setup.exe"
Type: dirifempty; Name: "{app}"

[Registry]
; Run Unscreen when Windows starts - the right hive depends on install scope.
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Unscreen"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startup; Check: IsAdminInstallMode
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Unscreen"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startup; Check: not IsAdminInstallMode

; First-run seeding: the app folds these into its config on very first boot
; (only when no config exists yet), so the installer's choices stick.
[INI]
Filename: "{app}\setup-flags.ini"; Section: "Setup"; Key: "AutoUpdate"; String: "1"; Tasks: autoupdate
Filename: "{app}\setup-flags.ini"; Section: "Setup"; Key: "AutoUpdate"; String: "0"; Tasks: not autoupdate
Filename: "{app}\setup-flags.ini"; Section: "Setup"; Key: "AutoStart"; String: "1"; Tasks: startup
Filename: "{app}\setup-flags.ini"; Section: "Setup"; Key: "AutoStart"; String: "0"; Tasks: not startup

[Code]
const
  UninstallKeyBase = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1';

var
  InstalledVersion: String;
  InstalledUninstallCmd: String;
  InstalledDir: String;
  AppMode: String;          // 'install' | 'upgrade' | 'repair' | 'maintenance' | 'downgrade'
  MaintPage: TInputOptionWizardPage;
  MaintChoice: Integer;     // 0 = Modify, 1 = Repair, 2 = Remove
  KeepDataPage: TInputOptionWizardPage;
  KeepData: Boolean;        // uninstaller: True -> do not touch %APPDATA%

{ Compare two dotted-decimal version strings. Returns -1/0/1. }
function CompareVersions(Version1, Version2: String): Integer;
var
  Packed1: Int64;
  Packed2: Int64;
begin
  if not StrToVersion(Version1, Packed1) then Packed1 := 0;
  if not StrToVersion(Version2, Packed2) then Packed2 := 0;
  Result := ComparePackedVersion(Packed1, Packed2);
end;

{ Read version/uninstall-command/install-dir from whichever view of the
  registry hosts the uninstall key (64-bit and 32-bit, HKLM and HKCU). }
function GetInstalledInfo(out Version: String; out UninstallCmd: String;
  out InstallDir: String): Boolean;
var
  Views: array of Cardinal;
  I: Integer;
begin
  Result := False;
  Version := '';
  UninstallCmd := '';
  InstallDir := '';
  Views := [HKLM64, HKLM, HKCU64, HKCU];
  for I := 0 to GetArrayLength(Views) - 1 do
  begin
    if RegKeyExists(Views[I], UninstallKeyBase) then
    begin
      RegQueryStringValue(Views[I], UninstallKeyBase, 'DisplayVersion', Version);
      RegQueryStringValue(Views[I], UninstallKeyBase, 'UninstallString', UninstallCmd);
      RegQueryStringValue(Views[I], UninstallKeyBase, 'InstallLocation', InstallDir);
      Result := True;
      Exit;
    end;
  end;
end;

{ Decide the flow and block a downgrade. }
function InitializeSetup(): Boolean;
var
  UninstallCmd: String;
  Dir: String;
  Compare: Integer;
begin
  Result := True;
  AppMode := 'install';
  InstalledVersion := '';
  InstalledUninstallCmd := '';
  InstalledDir := '';
  MaintChoice := 0;

  if (ParamStr(1) <> '') and (CompareText(ParamStr(1), '/MAINTENANCE') = 0) then
  begin
    AppMode := 'maintenance';
    Exit;
  end;

  if GetInstalledInfo(InstalledVersion, UninstallCmd, Dir) and
     (InstalledVersion <> '') then
  begin
    InstalledUninstallCmd := UninstallCmd;
    InstalledDir := Dir;
    Compare := CompareVersions(InstalledVersion, '{#MyAppVersionNumeric}');
    if Compare = 0 then
      AppMode := 'repair'
    else if Compare < 0 then
      AppMode := 'upgrade'
    else
    begin
      AppMode := 'downgrade';
      MsgBox(
        'You are attempting to downgrade Unscreen from ' + InstalledVersion +
        ' to {#MyAppVersion}, which is not supported.'#13#10#13#10 +
        'Uninstall the current app to install the older version.',
        mbCriticalError, MB_OK);
      Result := False;
    end;
  end;
end;

{ Brand the wizard per mode + build the maintenance page. }
procedure InitializeWizard();
begin
  case AppMode of
    'upgrade':
      WizardForm.WelcomeLabel2.Caption :=
        'Unscreen ' + InstalledVersion + ' is already installed on your computer.' + #13#10#13#10 +
        'This wizard will update it to {#MyAppVersion}. Your data is kept.';
    'repair', 'maintenance':
      WizardForm.WelcomeLabel2.Caption :=
        'Unscreen {#MyAppVersion} is already installed on your computer.' + #13#10#13#10 +
        'This wizard can modify, repair, or remove the installation.';
  else
    WizardForm.WelcomeLabel2.Caption :=
      'This will install Unscreen {#MyAppVersion} on your computer.' + #13#10#13#10 +
      'It is recommended that you close all other applications before continuing.';
  end;

  if (AppMode = 'repair') or (AppMode = 'maintenance') then
    MaintPage := CreateInputOptionPage(wpLicense,
      'Modify, repair or remove Unscreen',
      'What do you want to do?',
      'Unscreen ' + InstalledVersion + ' is already installed. Choose how to continue.',
      True, False)
  else
    MaintPage := nil;

  if (AppMode = 'upgrade') and (InstalledDir <> '') then
    WizardForm.DirEdit.Text := InstalledDir;
end;

{ Skip pages that do not apply to the flow. }
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;

  if PageID = wpLicense then
    Result := (AppMode <> 'install');

  if AppMode = 'upgrade' then
  begin
    if (PageID = wpSelectDir) or (PageID = wpSelectProgramGroup) or
       (PageID = wpSelectTasks) then
      Result := True;
  end
  else if (AppMode = 'repair') or (AppMode = 'maintenance') then
  begin
    if (MaintPage = nil) or (PageID = MaintPage.ID) then
      Result := False
    else if PageID = wpLicense then
      Result := True
    else if PageID = wpSelectDir then
      Result := True
    else if PageID = wpSelectProgramGroup then
      Result := True
    else if PageID = wpSelectTasks then
      Result := (MaintChoice <> 0);
  end;
end;

{ Intercept the maintenance page: capture the choice; "Remove" hands over to
  the uninstaller and abandons the wizard. }
function NextButtonClick(CurPageID: Integer): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;

  if not ((AppMode = 'repair') or (AppMode = 'maintenance')) then
    Exit;

  if (MaintPage <> nil) and (CurPageID = MaintPage.ID) then
  begin
    MaintChoice := MaintPage.SelectedValueIndex;
    if MaintChoice = 2 then
    begin
      // Remove: hand over to the uninstaller and abandon this wizard.
      if InstalledUninstallCmd <> '' then
      begin
        MsgBox('Uninstalling Unscreen ' + InstalledVersion + '...', mbInformation, MB_OK);
        Exec(RemoveQuotes(InstalledUninstallCmd), '', '', SW_SHOW,
          ewWaitUntilTerminated, ResultCode);
      end;
      Result := False;
      WizardForm.Close();
    end;
    // Modify (0) and Repair (1) fall through to the normal install steps;
    // ShouldSkipPage decides which pages they see.
  end;
end;

// On some systems the uninstall key written by Inno's internal registration is
// not visible afterwards, which breaks "Modify" in Apps & Features, upgrade
// detection and the uninstaller's own entry cleanup. ssPostInstall runs after
// Inno's own registration, so re-register the entry deterministically there
// (a no-op where Inno's write already landed).
procedure EnsureUninstallKey;
var
  Root: Integer;
begin
  if IsAdminInstallMode then
    Root := HKLM
  else
    Root := HKCU;

  if RegKeyExists(Root, UninstallKeyBase) then
    Exit;

  Log('Uninstall key not found after install; registering it explicitly.');
  RegWriteStringValue(Root, UninstallKeyBase, 'DisplayName',
    '{#MyAppName} {#MyAppVersion}');
  RegWriteStringValue(Root, UninstallKeyBase, 'DisplayVersion', '{#MyAppVersionNumeric}');
  RegWriteStringValue(Root, UninstallKeyBase, 'DisplayIcon',
    ExpandConstant('{app}\{#MyAppExeName}'));
  RegWriteStringValue(Root, UninstallKeyBase, 'Publisher', '{#MyAppPublisher}');
  RegWriteStringValue(Root, UninstallKeyBase, 'URLInfoAbout', '{#MyAppReleaseUrl}');
  RegWriteStringValue(Root, UninstallKeyBase, 'HelpLink', '{#MyAppReleaseUrl}');
  RegWriteStringValue(Root, UninstallKeyBase, 'URLUpdateInfo', '{#MyAppReleaseUrl}');
  RegWriteStringValue(Root, UninstallKeyBase, 'Inno Setup: App Path',
    ExpandConstant('{app}'));
  RegWriteStringValue(Root, UninstallKeyBase, 'InstallLocation',
    ExpandConstant('{app}\'));
  RegWriteStringValue(Root, UninstallKeyBase, 'UninstallString',
    '"' + ExpandConstant('{uninstallexe}') + '"');
  RegWriteStringValue(Root, UninstallKeyBase, 'QuietUninstallString',
    '"' + ExpandConstant('{uninstallexe}') + '" /SILENT');
  RegWriteStringValue(Root, UninstallKeyBase, 'ModifyPath',
    ExpandConstant('{app}\Unscreen-Setup.exe /maintenance'));
  RegWriteDWordValue(Root, UninstallKeyBase, 'NoRepair', 1);
  Log('Uninstall key registered explicitly at: ' + UninstallKeyBase);
end;

{ Refresh the version-bearing values of an existing uninstall key. Inno's own
  registration on upgrade is unreliable on some systems, leaving a stale
  DisplayVersion (e.g. 0.4.6) in Programs & Features after an update. }
procedure RefreshUninstallKey;
var
  Root: Integer;
begin
  if IsAdminInstallMode then
    Root := HKLM
  else
    Root := HKCU;

  if not RegKeyExists(Root, UninstallKeyBase) then
    Exit;

  Log('Refreshing version fields of existing uninstall key.');
  RegWriteStringValue(Root, UninstallKeyBase, 'DisplayName',
    '{#MyAppName} {#MyAppVersion}');
  RegWriteStringValue(Root, UninstallKeyBase, 'DisplayVersion',
    '{#MyAppVersionNumeric}');
  RegWriteStringValue(Root, UninstallKeyBase, 'DisplayIcon',
    ExpandConstant('{app}\{#MyAppExeName}'));
  RegWriteStringValue(Root, UninstallKeyBase, 'InstallLocation',
    ExpandConstant('{app}\'));
  RegWriteStringValue(Root, UninstallKeyBase, 'UninstallString',
    '"' + ExpandConstant('{uninstallexe}') + '"');
  RegWriteStringValue(Root, UninstallKeyBase, 'QuietUninstallString',
    '"' + ExpandConstant('{uninstallexe}') + '" /SILENT');
  RegWriteStringValue(Root, UninstallKeyBase, 'ModifyPath',
    ExpandConstant('{app}\Unscreen-Setup.exe /maintenance'));
  Log('Uninstall key refreshed with version {#MyAppVersionNumeric}.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Keep a copy of the installer in {app} so the Programs & Features
    // "Modify" button (AppModifyPath) always finds the maintenance entry
    // point, even without a network fetch.
    FileCopy(ExpandConstant('{srcexe}'), ExpandConstant('{app}\Unscreen-Setup.exe'), False);
    EnsureUninstallKey();
    RefreshUninstallKey();
  end;
end;

{ ------------ Uninstaller: ask whether usage data should stay ------------ }

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if not UninstallSilent then
  begin
    KeepDataPage := CreateInputOptionPage(wpWelcome,
      'Remove Unscreen data?',
      'Keep your history?',
      'Unscreen stores your usage history and settings in your user profile (AppData).',
      True, False);
    KeepDataPage.Add('Keep my history and settings');
    KeepDataPage.Add('Remove everything, including my history and settings');
    KeepDataPage.SelectedValueIndex := 0;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UninstallRoot: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if IsAdminInstallMode then
      UninstallRoot := HKLM
    else
      UninstallRoot := HKCU;

    // The entry may have been registered by the installer's own fallback
    // (EnsureUninstallKey) and is not tracked by the uninstall log, so remove
    // it here to leave no orphaned entry in Apps & Features.
    if RegKeyExists(UninstallRoot, UninstallKeyBase) then
    begin
      Log('Removing explicitly registered uninstall key.');
      RegDeleteKeyIncludingSubkeys(UninstallRoot, UninstallKeyBase);
    end;

    if (not UninstallSilent) and (KeepDataPage.SelectedValueIndex = 1) then
    begin
      if DirExists(ExpandConstant('{userappdata}\Unscreen')) then
        DelTree(ExpandConstant('{userappdata}\Unscreen'), True, True, True);
    end;
  end;
end;