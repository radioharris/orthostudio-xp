; What the uninstaller offers to take with it, read by tools/package/build.py into [Code].
;
; The program folder goes with the uninstall; the settings and the data OrthoStudio XP downloaded
; live apart, in %USERPROFILE%\.orthostudio (orthostudio.home.osxp_home), and can weigh tens of
; gigabytes. The uninstaller offers to take them and takes nothing unless the answer is yes, which
; is not what it starts on. Two things it never touches, and says so: a data folder chosen
; elsewhere, on another disk, which it only names; and the scenery already installed into
; X-Plane's Custom Scenery, which is the user's own work and outlives the tool that made it.

function OsxpHome: String;
begin
  Result := ExpandConstant('{userprofile}\.orthostudio');
end;

function ChosenDataDir: String;
var
  Lines: TArrayOfString;
  I: Integer;
  Line, Value: String;
begin
  // essential.data_dir of config.toml, read as a line rather than as TOML: the answer is shown,
  // never acted on, so a line this misreads costs a sentence and no data
  Result := '';
  if not LoadStringsFromFile(OsxpHome + '\config.toml', Lines) then
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
  Home, Data, Question: String;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  Home := OsxpHome;
  if not DirExists(Home) then
    Exit;
  Question :=
    'Also remove your OrthoStudio XP settings and the data it downloaded?' + #13#10 + #13#10 +
    Home + #13#10 + #13#10 +
    'This cannot be undone. Choose No to keep them for a later installation.' + #13#10 + #13#10 +
    'The tiles already installed into X-Plane''s Custom Scenery are yours, and are not touched.';
  Data := ChosenDataDir;
  if Data <> '' then
    Question := Question + #13#10 + #13#10 +
      'The data folder you chose is not touched either: ' + Data;
  if MsgBox(Question, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(Home, True, True, True);
end;
