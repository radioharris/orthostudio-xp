// The [Code] of the Windows installer (tools/package/build.py inno_setup_script).
//
// An OrthoStudio XP still running from the installation folder holds its python3.dll, and
// Windows refused to replace the file ("DeleteFile failed; code 5", a user installing again on
// 2026-09-14). Before the files are replaced or removed, it is asked to quit through its API, as
// an app asks an older engine or another installation (api/serve.py take_over); one that does
// not end is ended.
// build.py fills in the port of EngineQuitUrl.

const
  EngineQuitUrl = 'http://127.0.0.1:%PORT%/api/quit';

function QuitEngine(Force: Boolean): Integer;
var
  Http: Variant;
begin
  Result := 0;
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.SetTimeouts(2000, 2000, 5000, 5000);
    Http.Open('POST', EngineQuitUrl, False);
    Http.SetRequestHeader('Content-Type', 'application/json');
    if Force then
      Http.Send('{"force": true}')
    else
      Http.Send('{}');
    Result := Http.Status;
  except
    Result := 0;
  end;
end;

// The processes started from the folder's Python: the engine, and Triangle4XP or DSFTool.
function AppProcesses(const AppDir: String): Variant;
var
  Locator, Service: Variant;
  Pattern: String;
begin
  Locator := CreateOleObject('WbemScripting.SWbemLocator');
  Service := Locator.ConnectServer('.', 'root\CIMV2');
  Pattern := AddBackslash(AppDir) + 'python\';
  StringChangeEx(Pattern, '\', '\\', True);
  Result := Service.ExecQuery(
    'SELECT ProcessId FROM Win32_Process WHERE ExecutablePath LIKE ''' + Pattern + '%''');
end;

function CountAppProcesses(const AppDir: String): Integer;
begin
  try
    Result := AppProcesses(AppDir).Count;
  except
    Result := 0;
  end;
end;

procedure EndAppProcesses(const AppDir: String);
var
  Found: Variant;
  I: Integer;
begin
  try
    Found := AppProcesses(AppDir);
    for I := 0 to Found.Count - 1 do
      Found.ItemIndex(I).Terminate();
  except
  end;
end;

function AppEnded(const AppDir: String; Seconds: Integer): Boolean;
var
  I: Integer;
begin
  for I := 1 to Seconds * 4 do
  begin
    if CountAppProcesses(AppDir) = 0 then
    begin
      Result := True;
      exit;
    end;
    Sleep(250);
  end;
  Result := CountAppProcesses(AppDir) = 0;
end;

// '' once nothing runs from AppDir any more, else what to tell the user.
function StopOrthoStudio(const AppDir: String): String;
begin
  Result := '';
  if CountAppProcesses(AppDir) = 0 then
    exit;
  if QuitEngine(False) = 409 then
  begin
    if SuppressibleMsgBox('OrthoStudio XP is building tiles. Stop the build and go on? ' +
        'What is already built is kept.', mbConfirmation, MB_YESNO, IDYES) = IDNO then
    begin
      Result := 'OrthoStudio XP is building tiles. Let the build end, quit OrthoStudio XP ' +
        '(Quit, at the top right of its page), then start this program again.';
      exit;
    end;
    QuitEngine(True);
  end;
  if not AppEnded(AppDir, 20) then
  begin
    EndAppProcesses(AppDir);
    AppEnded(AppDir, 10);
  end;
  if CountAppProcesses(AppDir) > 0 then
    Result := 'OrthoStudio XP is still running. Quit it (Quit, at the top right of its page), ' +
      'then start this program again.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := StopOrthoStudio(ExpandConstant('{app}'));
end;

function InitializeUninstall(): Boolean;
var
  Message: String;
begin
  Message := StopOrthoStudio(ExpandConstant('{app}'));
  Result := Message = '';
  if not Result then
    SuppressibleMsgBox(Message, mbError, MB_OK, IDOK);
end;
