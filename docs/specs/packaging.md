# Packaging: the installers, with Python inside

Status: written with `tools/package/build.py`, `src/orthostudio/programs.py` and
`src/orthostudio/desktop.py` (decision 0012). Tests: `tests/test_package_build.py`,
`tests/test_programs.py`, `tests/test_desktop.py`; each installer is checked where it is built
(section 6). Origin: none in Ortho4XP, which is run from a checkout with the user's own Python.

## 1. The rule in plain language

A user downloads one file for their system and gets OrthoStudio XP without a terminal, without
`uv` and without installing Python: the app starts the engine and opens the page, as
`osxp serve --open` does from a checkout. Nothing is signed yet, so macOS and Windows ask for a
confirmation the first time (`README.md`).

## 2. What an installer holds

| | macOS (Apple Silicon, Intel) | Windows (x64) | Linux (x86-64) |
|---|---|---|---|
| file | `OrthoStudio-XP-<version>-macos-arm64.dmg`, `OrthoStudio-XP-<version>-macos-x86_64.dmg` | `OrthoStudio-XP-<version>-windows-x64-setup.exe`, and the same files as `.zip` | `OrthoStudio-XP-<version>-linux-x86_64.tar.gz` |
| installs | `OrthoStudio XP.app`, dragged to Applications | `%LOCALAPPDATA%\Programs\OrthoStudio XP`, for the current user, no administrator; Start menu entry, optional desktop icon, uninstaller | an `OrthoStudio-XP` folder wherever it is extracted; `install.sh` adds the applications menu entry, `install.sh --remove` takes it out |
| Python | `Contents/Resources/python` | `python\` | `python/` |
| started by | `Contents/MacOS/orthostudio` (shell script) | `python\pythonw.exe -m orthostudio.desktop` | `orthostudio-xp` (shell script) |

Uninstalling on Windows removes the program and nothing else, and says so
(`tools/package/uninstall_data.pas`). The settings and the data OrthoStudio XP keeps live apart,
in `%USERPROFILE%\.orthostudio` and in a data folder chosen elsewhere, and can weigh tens of
gigabytes: the uninstaller names both, so the room is not lost track of, and warns what removing
them by hand would cost. **The tiles installed into X-Plane are junctions into that folder, not
copies** (`orthostudio/install/packs.py`): taking it away empties X-Plane of every tile built
here and leaves dead links. An offer to remove it was written and taken out again, for that
reason: a checkbox nobody reads twice cannot be the thing standing between a user and hours of
building. An install over an older version asks nothing and takes nothing either. A silent
removal (`/VERYSILENT`) shows no message: a message box waited there for an OK nobody would click,
the removal done, in a Windows machine where `.orthostudio` existed (2026-10-02).

The Pascal the installer carries is only judged when it runs. Inno Setup compiles an unknown
constant without a word and refuses it in front of the user: `{userprofile}`, which it does not
have, reached one mid-uninstall as `Internal error: Unknown constant` and the message above was
never shown (2026-09-20). The environment is read with `GetEnv`, and a test walks every
`ExpandConstant` in the generated script against the constants Inno Setup documents.

The Windows setup program and its uninstaller first stop an OrthoStudio XP running from the
installation folder (`tools/package/stop_running.pas`, the `[Code]` of the script). A running
engine holds `python\python3.dll`, and a user installing again read "DeleteFile failed; code 5"
on it (2026-09-14). They ask the engine to quit with the page's own request, `POST /api/quit` on
port 8641 (a build running: they ask first, then `force`), wait up to 20 s for the processes
started from the folder's Python (the engine, Triangle4XP, DSFTool, found with WMI) to end, end
those still running, and give up with a message only if something still runs.

The macOS app declares `LSArchitecturePriority = [arm64]` and `LSRequiresNativeExecution`. Its
launcher is a script, which gives macOS no Mach-O to read the architectures from: without them the
Finder started it under Rosetta, and the Intel preference passed to every universal program below
it, though the engine's own Python has no Intel code. DSFTool and Triangle4XP then ran translated,
and macOS warned that the app "includes a component that will not work with a future release of
macOS" (the user's first installed app, 2026-09-14). Started from a terminal the same launcher runs
natively, which is why only a launch by macOS shows it (section 6). The doctor's `architecture`
check says it on any installation.

The app shows its page in a window of its own, through the web view the system already carries
(`orthostudio/window.py`, pywebview): WKWebView on macOS, WebView2 on Windows, WebKitGTK on Linux.
The launcher becomes that process rather than starting it aside, so that the window belongs to
this bundle; a window opened by a process started aside is called Python and carries Python's icon
(measured, 2026-09-20). The engine is the one put aside, in a process of its own
(`orthostudio.desktop.start_engine`): closing the window leaves a build running, and the engine
stops by itself a while after its last page. The engine starts only once the window is on screen,
behind the opening page, so that a system with no window to give is left as it was found and the
browser opens instead, as every version before 0.1.8 did. The doctor's `window` check says which it
was, and the page lists it: a system that could have a window and lacks a library is told which
one, with a link, and OrthoStudio XP installs nothing on a system it does not own. macOS carries
the web view and the app carries `pyobjc`, so a macOS build refuses an app whose `window` check is
not `ok` (`check_launch`).

The window opens at 1440x920, brought down to what the first screen has room for (`fits_the_screen`,
`ROOM_FOR_THE_SYSTEM`), and cannot be made smaller than 1024x700 (`MIN_SIZE`), where the Plan's two
columns stop fitting side by side. The page folds on its own under that width and must keep doing
so: a block that cannot shrink pushes the page wider than the window, which shows as a horizontal
scrollbar and as text cut off at the right, both of which a user met at the smallest window the app
allows (`tests/test_ui_static.py::test_the_final_report_folds_before_it_runs_out_of_room`).

It opens where it was left, the size it had (`window.back_where_it_was`, in pywebview's
`before_show`): it used to open where the system chose, on the screen of the shortcut rather than
the one the user works on, and was moved and resized at every start (TinkerNZ, 2026-10-03; "above
all on Windows"). macOS keeps the frame by name in the app's own defaults
(`NSWindow.setFrameAutosaveName_`, `PLACE_NAME`) as the window moves or resizes. Windows writes
`place.json` in the window's own folder as it closes (`keep_where_it_is`, in `closing`): its place
and size as a normal window (`Bounds`, or `RestoreBounds` when it is maximized or minimized), and
whether it was maximized. `RestoreBounds` alone follows only what code sets and the moment the
window leaves its normal state, not a move by hand: 0.1.22rc5 kept the place of each start for
ever (a Shadow, 2026-10-03). A place whose title
bar lies on no screen any more, the screen it was on unplugged, opens where the system puts it, as
on a first start (`grabbable`); one that cannot be read or written is said in `serve.log`.

The Windows installer offers the **WebView2 Runtime** to the machines without it: a task of its
own, ticked, that the user can turn down, shown only when Microsoft's own key says the runtime is
missing (`tools/package/webview2.pas`). Windows 11 carries it and, Microsoft writes, so do the
vast majority of Windows 10 machines. The 2 MB Evergreen bootstrapper is downloaded at build time
(`fetch_webview2`), so a Windows build reaches Microsoft once, as it already reaches PyPI; turned
down, nothing is installed and the app opens the browser.

Present is not enough: pywebview's WebView2 control asks the runtime for
`ICoreWebView2Environment10` as it starts, which came with **101.0.1210.39**
(`orthostudio.window.WEBVIEW2_MINIMUM`; `build.py` writes the same number into the Pascal, and a
test holds the two equal). A Shadow PC carried 100.0.1185.36 of 2022, never updated though its
Edge was current: the window opened empty, in its background colour, and pywebview's reason went
to a standard error that `pythonw` does not have (2026-10-01). So the app counts a runtime older
than that as none (`window.possible`, `webview2_too_old`): it opens the browser, the doctor's
`window` check names both versions, and the page tells the reader to run Microsoft's bootstrapper
as administrator. Run plainly it answers "already installed" and changes nothing; as administrator
it updated that PC's. The installer offers that update as a task of its own (`webview2update`,
ticked, `WebView2TooOld`), which its `[Code]` runs once the files are in, through
`ShellExec('runas', ...)`: Windows asks for an administrator's permission, and turned down or
failed, the setup goes on and says so in its log. Whatever pywebview itself warns about while the
window is up goes to `serve.log` (`window._ToNote`), so that a web view that will not start is
never again said to nobody. Linux asks for packages that need root
and differ between distributions: nothing is offered there, and the `window` check names them.
pywebview itself travels in the Linux archive like everywhere else, or naming those packages would
be advice that leads nowhere: they are useless without the package that uses them.

The Intel app (a user asked, 2026-09-17) is built on the same Apple Silicon Mac:
`build.py --machine x86_64` asks uv for the Intel CPython (`cpython-<version>-macos-x86_64-none`),
which uv runs under Rosetta to choose the Intel wheels of the lock, and the checks run it the same
way. Triangle4XP (its `CMakeLists.txt` asks for `arm64;x86_64`) and DSFTool are universal
binaries, and the build refuses one without the app's architecture (`lipo -archs`). The app
declares `LSArchitecturePriority = [x86_64]` without `LSRequiresNativeExecution`: it runs natively
on an Intel Mac and under Rosetta on Apple Silicon, where its check opens it; the doctor's
`architecture` check does not apply to it. It needs macOS 15: uv chooses the wheels for the macOS
the build runs on, and pyproj's Intel wheels ask for macOS 15, so the Intel app is built on macOS
15 (built on macOS 26, it asked for 15 too). `check_oldest_macos` holds each app to the oldest macOS
the README gives (`MAC_OLDEST`: 14.0 for Apple Silicon, 15.0 for Intel). A user installed the
Intel app of 0.1.19 on an Intel Mac under macOS 15.8 (GitHub issue 4, closed 2026-10-02).

In every one:

- a standalone CPython of the version `.python-version` names: the build uv installs
  (python-build-standalone), which runs from any folder;
- the runtime dependencies at the versions of `uv.lock`, the wheels the tests ran with, and
  OrthoStudio XP itself, not editable (`uv sync --frozen --no-dev --extra server
  --no-install-project` into that CPython, then OrthoStudio XP's own wheel, built from the
  checkout and installed with `uv pip install --no-deps --reinstall`: section 5);
- Triangle4XP, compiled for the system, and DSFTool, in `orthostudio/bin` (section 3);
- `licences/`: OrthoStudio XP's GPL, Triangle4XP's sources, notice of modifications and build
  file, as its licence requires with the binary, and DSFTool's notice. The wheels keep theirs in
  their `.dist-info`.

Not shipped: pip, the test suites of the dependencies (about 50 MB, most of them scipy's), the
command scripts the packages install (they name the build folder's Python and would run nothing
once moved), and what `uv sync` records about the checkout in OrthoStudio XP's `.dist-info` (its
path, its dates). The `.pyc` files are compiled at build time, their recorded paths relative to
the `python` folder, and the `_sysconfigdata` of the CPython gets back the `/install` prefix uv
replaced: no path of the build machine is left in an installer.

Every `.pyc` the app carries, the standard library's and every package's, is written by hash and
never checked against its source (`build.py` `BYTECODE_MODE`, `compileall -f --invalidation-mode
unchecked-hash`), so that no file time decides whether Python loads it. A timestamp `.pyc` holds
its source's mtime, and the Windows setup restores the files' times as the build machine's local
time, GitHub's runner being on UTC: at UTC+2 every `.py` was two hours older than its `.pyc` said,
and the first start after each install or update compiled the app again. In a Windows VM the
engine's imports wrote 969 `.pyc` in 12.6 s, then took 1.7 s; on a cloud PC, whose antivirus reads
each new file, the opening page said "Starting takes long" past 45 s (a user, 2026-10-02). Every
Windows version since 0.1.0 did so. The zip's times, local too, did the same, and the standard
library's own `.pyc` were out of date on every system, its files copied after they were written.

The setup then opens the app's code once, under its progress bar, before the app starts at the
end (`python -B -m orthostudio.warmup`): every module of OrthoStudio XP, and on Windows the .NET
and WebView2 files its window loads. The first time a new file is opened, Windows reads it from
the disk and its antivirus may inspect it: with the `.pyc` right, the first start of 0.1.21 after
its install still took about 10 s on a cloud PC, 7 of them loading the engine's code, against
about 3 s for the next ones (2026-10-02). It writes nothing, passes over what cannot load, and the
setup waits 5 minutes at most. A silent update does it too.

Every install keeps its log (`SetupLogging`): `Setup Log <date> #<n>.txt` in the user's temporary
folder (`%TEMP%`), each step with its time. An install on a cloud PC spent a minute and a half
before its first-start step, and nothing said where (2026-10-08).

Sizes, built by the release workflow: the `.dmg` about 67 MB (LZMA; 140 MB when it was written
with zlib, and the app takes 266 MB once installed), the Windows setup program 68 MB (LZMA), the
Linux `.tar.gz` 146 MB. A `.tar.xz` would save about a third of the last one, at some minutes of
processor per release; it is not worth it while the Linux archive is downloaded about a dozen
times a release.

The oldest macOS the app runs on is the highest `macosx_X_Y` of the wheels installed
(`LSMinimumSystemVersion`, computed and printed at build time): 14.0 on the macOS 14 build
machine of the release workflow, where numpy and scipy come built on Accelerate; 15.0 on a machine
whose uv cache holds orjson's macOS 15 wheel.

## 3. Where the programs are found (`programs.py`)

`installed_program(name)` is `orthostudio/bin/<name>` (`.exe` on Windows) when it is there and
executable. The modules that run a program read, in this order:

- Triangle4XP (`mesh.rule.triangle_binary`, and the doctor's check): `$OSXP_TRIANGLE4XP`, the
  installed copy, `Triangle4XP` on the PATH, the checkout's `native/triangle4xp/build`. The
  installed copy comes before the PATH: another Triangle4XP there, Ortho4XP's for instance, lacks
  the binary exchange of OrthoStudio XP's build (`mesh-triangle-io.md`).
- DSFTool (`overlays.find_dsftool`): the installed copy, then the checkout's
  `native/dsftool/<os>`.

A checkout has no `orthostudio/bin`: nothing changes for it.

## 4. What the app starts (`desktop.py`)

`python -m orthostudio.desktop [args]` runs the `orthostudio` command with `args`,
`serve --open --quit-when-closed` by default, its standard output and error appended to `serve.log` in the folder
`desktop.log_path()` gives -- `platformdirs.user_log_dir("OrthoStudio XP")` except on Linux, where
it is `$OSXP_HOME/log` beside everything else of ours: `~/Library/Logs/OrthoStudio XP` on macOS,
`%LOCALAPPDATA%\OrthoStudio XP\Logs` on Windows, `~/.orthostudio/log` on Linux. A
line gives the date and the arguments of each start. The log is UTF-8, what the engine a window
starts aside writes included (`PYTHONIOENCODING`, `desktop.start_engine`): on Windows a Python
writing to a file uses the system's code page, and that engine's lines came out in cp1252 among
the app's UTF-8 ones (a French Windows 11, 2026-10-02). That engine writes at the log's end
whatever else writes there (`desktop.append_only`): on Windows, handed the log as the window opened
it, it wrote where its own last line had ended, over the lines the window added since ("its window
closed"); a handle opened there for appending alone keeps them. Started from the Finder, a menu or `pythonw`,
the engine has no terminal; without the log, uvicorn's output would have nowhere to go.
`python -m orthostudio` runs the command itself (`__main__.py`), for a terminal.

Opening the app while OrthoStudio XP runs opens the running one's page (`api/serve.py`); *Quit* in
the page stops it. With `--quit-when-closed`, the app also stops by itself five minutes after the
last word from a page, unless a build runs or waits (`api/presence.py`): once the page is closed,
nothing shows the engine (`pythonw` has no window on Windows), and a user had to end it in the Task
Manager (2026-09-17).

**Open files.** Every command raises its soft limit of open files to 8192, within the hard limit
(`fsutil.raise_open_files_limit`, in the CLI's callback), and the processes it starts inherit it.
macOS gives an app started from the Finder or the Dock launchd's 256, where a terminal has far
more: an image download of 192 connections (Arc@'s measured ceiling, started at once) with the
files it writes went past it, and textures of a user's tile failed with "Too many open files"
(2026-09-15). Windows has no such limit.

**The opening page** (user request, 2026-09-15: the app showed nothing while the engine started,
a long while on a first launch or in a virtual machine). Started without arguments, and before its
long imports, `desktop.main` checks whether something listens on the engine's port (8641). When
nothing does, `open_while_starting` serves a small page from a free port of the loopback (two
minutes) and opens it: "Opening OrthoStudio XP…" (in French for a French browser), a spinner, and
after 45 s the path of `serve.log`. The page asks the engine's port every 0.4 s (`fetch` in
`no-cors` mode: any answer means it listens) and replaces itself with the engine's page; the engine
then starts with `--no-open --quit-when-closed`. Being on another port, it never holds the engine's. When something
already listens, the start is the usual one: `serve --open` opens the running OrthoStudio XP, or
asks an older one to stop first. The data stay in `~/.orthostudio` (`$OSXP_HOME`): uninstalling the app leaves
them.

**How long a start took** (`startclock.py`). A cloud PC took more than 45 s to open the app after
an install, and the log could not say where the time went: no line of it had a time between the
window's and uvicorn's (2026-10-02). As the window starts its engine, it writes
`its window opens: Python 0.9 s, loading the window 2.5 s`. That is Python's own start, from the
creation of its process to `orthostudio.STARTED_AT` (the package's first import), known on
Windows alone (`GetProcessTimes`), then the window's loading: pywebview, the toolkit under it and,
on Windows, .NET and WebView2's own files. It hands the engine the time it starts it
(`OSXP_LAUNCHED_AT`, which the engine takes out of its environment). Once its port is open, the
engine writes `listening, 26.1 s after the window started it: Python 1.2 s, loading 24.3 s,
setting up 0.4 s, opening its port 0.2 s` (`serve.listening_line`). The parts are Python's own
start, the loading of its code (its imports), its setting up (`create_app`, an older engine asked
to quit included) and uvicorn's start up to the open port. Started otherwise (a terminal, the
browser's start), it counts from its own code. The page's first `POST /api/presence` then writes
`its page is open, 1.9 s after its port opened`. What the page waits for longer than a second
(`startclock.SLOW_S`) is named with its time:

- an answer of the API, as in `GET /api/status answered in 12.3 s`. The base map's tiles are left
  out, since their time is the network's;
- a part of the status, as in `the status's checks took 3.1 s`;
- a check of the doctor, as in `the doctor's triangle4xp check took 2.8 s`.

`doctor --json` is as it was: the checks' times stay out of its document.

## 5. The build (`tools/package/build.py`)

Run on the system the installer is for, after Triangle4XP is built; Inno Setup 6 for Windows.

1. `uv python install --no-bin <version>` into `dist/package/python-cache-<machine>` (nothing is
   added to the user's PATH), the CPython copied into the installer's folder;
2. the dependencies with `uv sync --no-install-project`, then OrthoStudio XP's wheel, built by
   `uv build` from the checkout and installed with `uv pip install --reinstall`: given the project
   too, `uv sync` took a wheel from uv's cache, which still counted as current after a change of
   the code, the version being unchanged (an installer missed the doctor's newest check); then the
   packed package is compared with the files git tracks in `src/orthostudio`, byte for byte, pruned
   and compiled;
3. the programs into `orthostudio/bin`, the licences, the launcher, the icon (`icon.py`: `.icns`,
   `.ico`, `.png`);
4. the installer: `hdiutil create` of a folder holding the app and a link to `/Applications`;
   Inno Setup's `ISCC` on a generated script (`inno_setup_script`, a fixed `AppId` so that an
   update replaces the installed app) and a `.zip`; a `.tar.gz` that keeps the symbolic links.
   The script sets `RedirectionGuard=no`: Inno Setup 6.7 puts its setup program under Windows'
   RedirectionGuard, and the app its last page opened inherited it and could not follow the
   junctions it makes in Custom Scenery, so no tile could be installed (2026-09-15; `install.md`
   4). The mitigation protects an elevated setup from junctions planted by an unprivileged user;
   this one runs as that user, without elevation.

`--offline` takes the packages from uv's cache only. `tools/package/checkout_app.py` is another
thing: a macOS app that runs a checkout's `.venv`, for development.

## 6. Checks

`build.py --check`, run by the release workflow on each system, first starts the packed engine from
its own Python, with an empty `$OSXP_HOME`, no `$OSXP_TRIANGLE4XP` and no user site: that Python is
standalone (its prefix and standard library are in the installer, no `pyvenv.cfg`: run through
`uv run`, an early build packed the project's `.venv`, whose Python lives outside, and a check on
the build machine could not see it); the doctor's Python, encoder and HTTP client checks pass and
it finds the packed Triangle4XP; `find_dsftool` is the packed DSFTool; every module of the package
imports (nothing the pruning removed is needed); `osxp serve --check` starts the server on a free
port, reads `/api/status` and the page, and stops.

It then installs the installer itself in a throw-away place and starts the app through the entry a
user starts, `doctor --json` (read back from `serve.log`: it must find the Triangle4XP installed
with the app) and `serve --check`:

- macOS: the `.dmg` mounted read-only, `Contents/MacOS/orthostudio` of the app in the image; then
  a copy of the app, under another bundle identifier (given an app whose identifier and version an
  installed app shares, `open` starts the installed one), opened by macOS itself (`open -W -n
  --env HOME=...`, `doctor --json`), whose `architecture` check must be `ok`;
- Windows: the setup program run silently into a temporary folder (`/VERYSILENT /DIR=`),
  `python\pythonw.exe -m orthostudio.desktop`; then, with the installed engine started and left
  running, the setup program run again over it, and the uninstaller, each of which must stop the
  engine and succeed; the uninstaller must remove the app;
- Linux: the archive extracted, `orthostudio-xp`, then `install.sh`, whose menu entry must name
  that launcher, and `install.sh --remove`, which must take it out.

The checks build no tile, and they run on the build machines: an installer opened by hand on a
machine that never had OrthoStudio XP, and tiles built from the installed app and flown over, are
checks a person makes before a release (section 7).

## 7. Release workflow (`.github/workflows/release.yml`)

On a tag `v*`, and by hand: one job per installer (macOS 14 for the Apple Silicon app; macOS 15,
with `--machine x86_64` after installing Rosetta, for the Intel app; Windows; Ubuntu 22.04: Triangle4XP
compiled there runs on older glibc) builds and checks its installer. A private repository builds
the Windows installer alone: its minutes are counted, macOS's ten times over, and the Mac app of a
release candidate is tried from a checkout (2026-10-03). On a tag, a first job makes the
release as a draft (a pre-release when the tag has a suffix, as in `v0.2.0-rc.1`), with
`docs/releases/<version>.md` as its notes when that file exists (`v0.1.0`:
`docs/releases/0.1.0.md`), else one line saying the installers are not signed. Each installer goes
into the draft as soon as it is built (`gh release upload`), and a last job publishes it once all
are in: no one sees a release with an installer missing, and a build that fails leaves a draft
nobody sees. No artifact carries them from job to job: artifacts count against the account's
storage, which builds in a private repository filled, and a release then could not be made at all
("Artifact storage quota has been hit", 2026-10-03); the files of a release do not count. A run by
hand keeps its installers as the run's artifacts, for a day.

Before a release: `version` in `pyproject.toml` set to the tag's (the installers are named after
it), the notes written, and the checks a person makes, which no workflow can
(`docs/testing/before-a-release.md`: installers on machines that never had OrthoStudio XP, tiles of
varied kinds flown over in X-Plane).

## 8. Not done

- Signing: Developer ID and notarisation for macOS, a code-signing certificate for Windows. Both
  need accounts of the publisher (decision 0012).
- Windows and Linux on ARM.
- The Intel app checked on an Intel Mac: the release workflow checks it under Rosetta only. A user
  installed the Intel app of 0.1.19 on an Intel Mac under macOS 15.8 (GitHub issue 4).
- Automatic updates: a new version is installed over the old one.
