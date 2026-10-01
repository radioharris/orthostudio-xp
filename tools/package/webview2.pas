// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The Microsoft WebView2 Runtime, read by tools/package/build.py into the installer's [Code].
//
// OrthoStudio XP shows its page in a window of its own through WebView2 (orthostudio/window.py).
// Windows 11 carries it, and so do the vast majority of Windows 10 machines; for the few that do
// not, the installer offers to fetch it, ticked but never forced, and the app opens the browser
// instead when it is turned down. Microsoft's own way of telling whether it is there: the pv value
// of the runtime's key, per machine or per user, present and above 0.0.0.0.
// https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution
//
// Present is not enough: the window does not start on a runtime older than %WEBVIEW2_MINIMUM%
// (orthostudio.window.WEBVIEW2_MINIMUM, written here by build.py). A Shadow PC carried one of 2022
// that had never been updated, and the window opened empty (2026-10-01). For such a runtime the
// installer offers the update, which Microsoft's bootstrapper refuses ("already installed") unless
// it runs as administrator: Windows asks for that permission, and turned down, nothing happens and
// the app opens the browser.

// The runtime's version under Key, or '' when it is not there.
function WebView2Version(Root: Integer; Key: String): String;
var
  Version: String;
begin
  Result := '';
  if RegQueryStringValue(Root, Key, 'pv', Version) then
    if Version <> '0.0.0.0' then
      Result := Version;
end;

// The next number of a dotted version, taken off its front: '101.0.1210.39' gives 101 and leaves
// '0.1210.39'. What is not a number reads 0.
function TakeVersionPart(var Rest: String): Integer;
var
  Dot: Integer;
begin
  Dot := Pos('.', Rest);
  if Dot = 0 then
  begin
    Result := StrToIntDef(Rest, 0);
    Rest := '';
  end
  else
  begin
    Result := StrToIntDef(Copy(Rest, 1, Dot - 1), 0);
    Delete(Rest, 1, Dot);
  end;
end;

// Whether the dotted version Older comes before Newer, part by part: 100.0.1185.36 comes before
// 101.0.1210.39.
function VersionBefore(Older: String; Newer: String): Boolean;
var
  RestOlder, RestNewer: String;
  Part, PartOlder, PartNewer: Integer;
begin
  Result := False;
  RestOlder := Older;
  RestNewer := Newer;
  for Part := 1 to 4 do
  begin
    PartOlder := TakeVersionPart(RestOlder);
    PartNewer := TakeVersionPart(RestNewer);
    if PartOlder <> PartNewer then
    begin
      Result := PartOlder < PartNewer;
      Exit;
    end;
  end;
end;

// The runtime this machine carries: the newer of the per machine and per user ones, '' for none.
// The installer is 64-bit only (ArchitecturesAllowed), so these are the two keys to read.
function WebView2Installed: String;
var
  Machine, User: String;
begin
  Machine := WebView2Version(HKLM,
    'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
  User := WebView2Version(HKCU,
    'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
  Result := Machine;
  if (Result = '') or ((User <> '') and VersionBefore(Result, User)) then
    Result := User;
end;

function WebView2Missing: Boolean;
begin
  Result := WebView2Installed = '';
end;

function WebView2TooOld: Boolean;
var
  Found: String;
begin
  Found := WebView2Installed;
  Result := (Found <> '') and VersionBefore(Found, '%WEBVIEW2_MINIMUM%');
end;

// The update, as administrator: Windows asks the user first. Turned down or failed, the setup goes
// on; it is written in the setup's log and the app opens the browser.
procedure UpdateWebView2;
var
  ResultCode: Integer;
begin
  ExtractTemporaryFile('%WEBVIEW2_EXE%');
  if not ShellExec('runas', ExpandConstant('{tmp}\%WEBVIEW2_EXE%'), '/silent /install', '',
      SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then
    Log('The WebView2 Runtime was not updated: ' + SysErrorMessage(ResultCode))
  else
    Log('The WebView2 Runtime update ended with code ' + IntToStr(ResultCode));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('webview2update') then
    UpdateWebView2;
end;
