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

function WebView2Found(Root: Integer; Key: String): Boolean;
var
  Version: String;
begin
  Result := False;
  if RegQueryStringValue(Root, Key, 'pv', Version) then
    Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2Missing: Boolean;
begin
  // the installer is 64-bit only (ArchitecturesAllowed), so these are the two keys to read
  Result := (not WebView2Found(HKLM,
      'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'))
    and (not WebView2Found(HKCU,
      'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'));
end;
