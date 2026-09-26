// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// What the uninstaller says about what it leaves, read by tools/package/build.py into [Code].
//
// It removes the program and nothing else. The settings and the data OrthoStudio XP downloaded
// live apart, in %USERPROFILE%\.orthostudio (orthostudio.home.osxp_home), and can weigh tens of
// gigabytes, so leaving them without a word leaves the user wondering where the room went. It
// names the folder instead, and warns what removing it by hand would cost: the tiles installed
// into X-Plane are junctions into that folder (orthostudio/install/packs.py), not copies, so
// taking it away empties X-Plane's scenery of every tile built here and leaves dead links behind.
//
// An offer to remove it was written and taken out again. A checkbox nobody reads twice cannot be
// the thing standing between a user and hours of building.

function OsxpHome: String;
var
  Profile: String;
begin
  // GetEnv, not ExpandConstant: there is no {userprofile} constant, and a constant that does not
  // exist is only found when the uninstaller runs. It compiled, CI was happy, and a user was
  // shown "Internal error: Unknown constant" instead of the message (2026-09-20).
  Result := '';
  Profile := GetEnv('USERPROFILE');
  if Profile <> '' then
    Result := Profile + '\.orthostudio';
end;

function ChosenDataDir: String;
var
  Lines: TArrayOfString;
  I: Integer;
  Line, Value: String;
begin
  // essential.data_dir of config.toml, read as a line rather than as TOML: the answer is only
  // ever shown, so a line this misreads costs a sentence and nothing else
  Result := '';
  if (OsxpHome = '') or (not LoadStringsFromFile(OsxpHome + '\config.toml', Lines)) then
    Exit;
  for I := 0 to GetArrayLength(Lines) - 1 do begin
    Line := Trim(Lines[I]);
    if Pos('data_dir', Line) = 1 then begin
      Value := Trim(Copy(Line, Pos('=', Line) + 1, Length(Line)));
      StringChangeEx(Value, '"', '', True);
      StringChangeEx(Value, '''', '', True);
      StringChangeEx(Value, '\\', '\', True);
      Result := Trim(Value);
      Exit;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data, Where, Note: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  if (OsxpHome = '') or (not DirExists(OsxpHome)) then
    Exit;
  Data := ChosenDataDir;
  if Data <> '' then
    Where := OsxpHome + #13#10 + Data
  else
    Where := OsxpHome;
  Note :=
    'OrthoStudio XP is removed. Your settings, and the tiles and downloads it keeps, are left ' +
    'where they are:' + #13#10 + #13#10 + Where + #13#10 + #13#10 +
    'Install OrthoStudio XP again and it finds them. To free the room, remove them yourself, ' +
    'knowing what it costs: the tiles you installed into X-Plane are junctions into that folder, ' +
    'not copies, so removing it takes them out of X-Plane as well.';
  MsgBox(Note, mbInformation, MB_OK);
end;
