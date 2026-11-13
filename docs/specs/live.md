# OrthoStudio XP Live

Ortho imagery streamed into X-Plane 12 while it flies, without FUSE. The textures are ordinary files
on the disk, and a streamer keeps them built at the level X-Plane will read of each, ahead of the
aircraft and around the camera. X-Plane reads files, nothing else: if the streamer stops, X-Plane
goes on flying over what is on the disk, and there is nothing to freeze on. At a flight's start, a
small plugin holds X-Plane on its loading screen until the area is built.

It comes from X-StreamOrtho, a research project whose probe flights measured how X-Plane reads
ortho textures ([live/probe-flight-1.md](live/probe-flight-1.md),
[live/probe-flight-2.md](live/probe-flight-2.md)) and how it loads a flight
([live/hold-test.md](live/hold-test.md)). Its replayable test flights are in
[live/flights.md](live/flights.md). The users' guide is [../live.md](../live.md).

- **Switched on by default** (`ENABLED_BY_DEFAULT`): `enabled = false` in
  `~/.orthostudio/live.toml`, its own file, switches it off. It adds nothing to OrthoStudio XP's
  settings or command line: it is a part of the app's frame (see "One app with OrthoStudio XP").
- **macOS (Apple Silicon and Intel), Windows and Linux**, which the user asked for (2026-09-29,
  2026-10-01): the plugin is built for each, its loading window is Cocoa, Win32 or X11 (see "A
  flight's start"); Live reads each system's own list of programs and starts X-Plane as a double
  click does there (`orthostudio/live/scenery.py`); its encoders are forked by a fork server on
  macOS and Linux, and are new Pythons on Windows, which cannot fork (`orthostudio/live/pools.py`).
  Its files work on every kind of disk its folder may be on, APFS, NTFS or ext4 (see "The
  pack"). Windows asks two things more: a datagram sent to a port nobody listens on (Live asks
  X-Plane's before X-Plane is up) makes the socket's next read fail there, which macOS and Linux
  never do (`xpudp.no_port_unreachable` tells Windows not to, and the reads pass the error all the
  same: before it Live would have stopped at its first ask); Python's process pools hold 61
  processes at most there (`machine.WINDOWS_POOL_MAX`); and Windows Search, which listed Live's
  folder's hundreds of thousands of files for whole test flights on a Shadow PC (half a core and
  more, 2026-10-02), is kept out of it by a rule of its crawl scope that Live adds at each start
  and region install, no administrator's rights needed (`winsearch.py`; no mark on the files keeps
  them out: those not to be content-indexed, hidden or system are listed all the same). Flown with X-Plane on a Mac only:
  Windows and Linux are built and tested with a stand-in X-Plane (2026-10-01), Live's whole test
  suite runs on Linux (arm64 and x86-64, in Docker), on an Intel Python under Rosetta and on
  Windows 11 (in a virtual machine), and the Windows plugin under Wine; not yet flown.
  `tools/live/windows_kit.py` makes the kit a user tries it with on a Windows PC: Live's tests
  there, the app in its one window, and the replayable test flights of the app
  ([live/flights.md](live/flights.md)).
- **Code**: `src/orthostudio/live` (the engine and `python -m orthostudio.live`),
  `native/xplane_plugin` (the X-Plane plugin), `tests/test_live_*.py`.
- **Reused from OrthoStudio XP**: X-Plane's detection and its reading and writing of
  `scenery_packs.ini` (Live's own rule lists `z_osxp_live`: "Live's line in the list"); its
  window, the guard of its API and its style sheet, for Live's window.
- **Live's folder** holds `pack` and `chunks.sqlite`: `~/.orthostudio/live` unless the window's
  settings choose another (`folder` in `live.toml`), on a disk in APFS, NTFS or ext4 (see "The
  pack"). Not OrthoStudio XP's data
  folder: that one may move to a disk for a while, and a pack following it would be set up again
  there, what it held left behind (a user, 2026-09-27). A folder whose disk is not plugged in is
  never made on the computer's own disk: Live refuses to start.
- **The mesh** is AutoOrtho's, every region installed (`z_ao_eur`, `z_ao_na`...), read only, for
  now.

## Commands

```bash
uv run python -m orthostudio.live ui                  # the Live window: Start, Stop, what it does, its settings
uv run python -m orthostudio.live stream              # then start X-Plane; Ctrl-C to stop (after quitting X-Plane)
uv run python -m orthostudio.live stream --from LFMN  # also build around LFMN straight away
uv run python -m orthostudio.live stream --fresh      # as the first time: nothing built, nothing downloaded (to try a new area)
uv run python -m orthostudio.live reset               # the same, without streaming
```

`--fresh` and `reset` turn every texture the streamer recorded as built back into a name of the
neutral texture, which takes seconds. They also delete the
downloaded imagery. `--fresh` does not build the last departure ahead either.

Quit any other program streaming ortho first: Live refuses to start beside XPME or AutoOrtho,
found by their processes' names (XPME's window « XPlane Map Enhancement » and its server
« XplaneMapEnhancement », AutoOrtho's « autoortho ») or by the folders they mount (AutoOrtho's
`z_ao_*` regions, the folder XPME's `XPME_*` packs link into): `scenery.ortho_streamers`.
Start the streamer before X-Plane: X-Plane lists
scenery packs and loads plugins when it starts. The streamer links its pack (`z_osxp_live`), checks
its line and installs its plugin (`Resources/plugins/OrthoStudio XP Live`) as it starts; as it
stops, once X-Plane has quit (X-Plane opens its textures through the pack's link), it unchecks the
line where it is. The plugin stays, idle without Live.

## The window

`uv run python -m orthostudio.live ui` serves Live's page on the loopback (port 8642) and shows it
in a window of its own, built with OrthoStudio XP's parts as they are: `orthostudio.window.show`,
the guard of its API (the `Host` allow-list, no request from another website, JSON only), and its
style sheet, served under `/static`. The page is Live's (`orthostudio/live/ui`: plain HTML and ES
modules, English and French, English unless the switch chose French, as OrthoStudio XP).

- **The API** is a router of its own under `/api/live` (`orthostudio/live/api.py`), which
  OrthoStudio XP's engine can include as it is the day Live comes into its window: `status`
  (the state, the last second's figures, the checks, the settings), `log`, `start`, `stop`,
  `leave`, `uninstall`, `settings`. The page asks for the status every second. `quit` is the
  Windows installer's, before it replaces or removes the app's files (`stop_running.pas`): the
  window goes once the stream is out, at once while no stream feeds a running X-Plane; while one
  does, 409, unless `force`, which stops without waiting for X-Plane, the pack left installed.
  `ui --no-open` serves the page without opening it, for the installer's check.
- **The log's file** (`orthostudio/live/logfile.py`): `LogLive.txt` at the top of the X-Plane
  folder, beside X-Plane's `Log.txt`, as the user asked ("un log simple à la racine... du genre
  LogLive.txt"; OrthoStudio XP's own, in `~/Library/Logs`, they found hard to find). The window
  kept its log in memory only, gone when it closed, twice before it could be read (2026-09-29).
  - It is written again each time the window opens, as X-Plane's is. A header says which Live
    (version and code mark), since when (`Live started on`, `time.ctime`'s words) and on what
    computer. Then every line of the window's log follows with its time, flushed as it comes.
  - Under "streaming into", a start says where its seconds went (`session.StartClock`, the user,
    2026-10-31: « 10 secondes AU MOINS »): `start in 9.10 s: checks 0.02, region 0.40, ...`, each
    step from the click's "starting": the checks, the region's pack, the user's own tiles, the
    overlays, the plugin and its airports, X-Plane's texture quality, the computer's share, the
    season, the photos' store opened, the regions' textures read.
  - The runs before go to `log` in Live's folder, `LogLive <start>.txt`, the last 10.
  - Without an X-Plane folder Live can write in, it is in Live's folder. With `OSXP_HOME` set (a
    test window), it is always there, never in the user's X-Plane.
  - The log panel names the file with a Show button (`status.log_file`). That button, as Live's
    folder's, names the file manager of the computer (`status.platform`: Finder, File Explorer,
    the folder on Linux).
- **The stream** runs in a thread of the window's process (`orthostudio/live/control.py`), through
  the very steps of `stream` (`orthostudio/live/session.py`: prepare, follow, finish). Its states:
  idle, starting, streaming, stopping, and leaving (stopped while X-Plane runs: the pack comes out
  once X-Plane quits, unless left installed). While it streams, Live's process forks neither its
  encoders (see the encoding processes in the loop) nor commands: whether X-Plane runs and when it
  started come from macOS's list of processes (`scenery.xplane_running`, `xplane_started_at`,
  libproc; the newest of two, one quitting as the next starts), not from `ps` or `pgrep`, and
  X-Plane is launched by posix_spawn, which does not fork (`scenery._spawn`). When the encoders'
  pool broke, X-Plane was taken for gone, most likely a `ps` that died the same way (a `ps` that
  fails reads as no X-Plane), and the pack came out from under it; the next flight, a `pgrep` died
  so as X-Plane arrived.
- **The figures** are the status line's, counted once (`Streamer.snapshot`, `progress_report`):
  what X-Plane sends, the start it loads and whether it holds X-Plane, the textures queued, being
  built and built by level, the pieces downloaded, the sources refusing this computer, the rate,
  the disk.
- **Quitting** is OrthoStudio XP's (the user asked, 2026-10-03): its Quit button at the right of
  the top bar, and its question in the page, the window in front (`live.js quitLive`, OrthoStudio
  XP's `confirmQuit`), Stay or Quit, saying that a stream stops and that Live stays until X-Plane
  quits; Cmd+Q, the app's menu and the Dock's quit the app, both windows (`web.app_may_quit`). The answer is `POST /api/live/close` (`LiveControl.close`): a
  stream stops as Stop stops it, and the window goes once the pack is out (`LiveControl.gone`). On
  Windows and Linux closing the window is quitting it, and asks first, in a box, only while a
  stream runs, as OrthoStudio XP asks only while a build runs (`web._live_window`).
- **The settings** are the `[stream]` table of `live.toml`, beside the switch, which is written
  back as it was read: the disk space, the imagery source, Live's folder. `stream` takes them as
  its defaults: a budget set in the window holds in a terminal too. The reach of outside views and
  the texture quality are not settings (the command line keeps them, for tests): less than X-Plane
  reads shows the neutral colour, green-grey (a user, 2026-09-27). The texture quality is X-Plane's
  own, read in X-Plane and followed as it changes (see "X-Plane's texture quality").
- **The X-Plane folder** is not one of them: it is `essential.xplane_dir` in `config.toml`, one
  setting with Build's (`chosen_xplane`). Live's Settings read and write it through
  `/api/live/xplane-folder`, saved before Live's own settings since Live's folder is checked against
  this X-Plane. It changes only while Live is idle and X-Plane is not running; Live's lines in the
  X-Plane left are unchecked (its plugin stays there, idle), and it goes into the new one at the
  next start. Build's page reads
  the value again when its Settings are shown and before its Save (`followXplaneFolder`), so that a
  Save never writes back the folder it read earlier.
- **The page** is made of OrthoStudio XP's own parts (its Settings' cards and fields, its dialogs),
  so that the spacing is the same, and a click on a field's title is left to its text as
  OrthoStudio XP leaves it (`isTitleClick`): the title can be selected.
- **Buttons**: Start X-Plane, enabled once Live streams (X-Plane sees the pack and loads the plugin
  only if they are there when it starts); Delete, for the built textures (back to the neutral
  texture), the downloaded imagery, or both, while nothing streams, in Settings and from the trash
  at the end of Live's buttons (OrthoStudio XP's `.btn-trash`), the same dialog (the user,
  2026-10-31: in place of a From scratch box, which a Prepare starting Live did not read; the
  command line keeps `--fresh`); Choose and Show in the Finder for Live's folder and X-Plane's, with
  OrthoStudio XP's folder dialog.
- **The zoom**, AutoOrtho's max_zoom and max_zoom_near_airports (the latter for the mesh's ZL18
  textures, AutoOrtho's rule), sizes each texture's file as AutoOrtho sizes its DDS: smaller below
  the mesh's zoom, 8192 px one zoom above at most (level -1), from the pack's layout on
  (region.json `"layout": 3`). The size is set before X-Plane reads anything and never changes
  while it runs: X-Plane reads a file's size when it loads its tile and often keeps it (an
  fs_usage trace in flight, 2026-09-28: files grown from 4096 to 8192 px were read at 21 km, then
  never again). A new zoom, at the next start, X-Plane not running, touches no texture
  (`region.set_zooms`, an instant): the levels recorded as built stay those the files hold, each
  lowered only when its file is set to the new size (a zoom set back before the aircraft came
  near would otherwise build again the 8192 px levels the files still held: 29 textures at a start
  round Toulouse, 2026-10-10), the map and its counts showing meanwhile what X-Plane will show
  (`mapdata.shown`: no sharper than the new size), the squares made before lose their terrain
  files at once (the folder set aside, removed behind by a thread: `remove_aside`), and each
  texture takes its new size as its square is made (`names.Names.make`, its "fit" operations,
  in one process, at a start too: shared between the builder's processes they took the encoders
  from the photos X-Plane waits for, X-Plane going on 7 s later on an 8-core Shadow PC at a cold
  start and 14 s later at a new zoom, 2026-10-11; then its terrain files, so that X-Plane never
  reads one at its old size; a start with a new zoom measured against the same flight without
  one on a Mac, 2026-10-11: the squares' making hidden in X-Plane's wait for the photos, the same
  processor, memory and frames in flight): neutral ones become names of the neutral texture of the new size, built
  ones keep their levels from the one built down (the file's size read from its header), nothing
  downloaded; one the builder has taken, to be written at its old size, makes its square wait a
  moment (`SquareWaitError`), one only queued is set at once (waiting for every queued texture of
  a square held a start's square past `GIVE_UP_S`, X-Plane going on without it: three starts,
  2026-10-10), one being set is not written meanwhile (`Names.fitting`), its level said to the
  builder before it may take it. Made coarser for
  the disk or copied up, a texture is read at its header's size (`texture.downgrade`,
  `copy_up`). Laying every texture out at the start took 41 to 67 s for the 44,305 of a pilot's
  flights, longer with each, even over twelve processes 18 s (the user: « si j'ai 100000
  textures ... il va appliquer aux 100000 », 2026-10-10). Layouts 1 (every file at its size,
  without copies) and 2 (4096 px files that grew in flight, `grown` in `_live/ortho.json`) are
  laid out again once, whole (`region.relayout`, over this computer's encoders), the window
  saying so (status `relayout`, `session.relayout_due`). The window chooses both on Live's screen,
  on the imagery source's line (the user, 2026-10-09), as one list of three combinations since
  2026-10-12 (the user: « sélectionner 16/18 et 17/19 et puis c'est tout »): 16/18 standard, 16/19
  sharper airports, 17/19 sharpest (`live.js ZOOM_CHOICES`); 14 and 15 went, a saving nobody was
  measured to need, none other offered (Live came out without them); the two lists of before kept in
  comments (`index.html`, `live.js`) should they come back. What each does is in its hover
  (`drawZooms`), saved as soon as chosen and locked while Live runs, as the source (`chooseZoom`),
  the disk space raised to their floor and said under it (`zoom-disk`). The Settings tab has no
  Zoom card since 2026-10-31 (the user: it only said again what the hover says); the pink test is
  `tint_8192` in `live.toml` alone. The engine still takes any zoom (`--max-zoom`). Its cards read
  the most needed first (the user, 2026-11-11), down the first column then the second (CSS
  columns): X-Plane's folder, the mesh by region, the disk space, Live's folder; then what a
  flight shows: roads, forests and buildings, your own tiles, the season, the night lights, the
  ground grain; then the flight plan and what the app opens on.
- **A start** waits for everything X-Plane reads while the flight loads, the 8192 px level within
  15 km included (see "The 8192 px level"), its rings with the margin they have in flight (11 %,
  `DEFAULT_MARGIN`; the aircraft standing has it too since the review of 2026-11-12: standing
  without it, a jump X-Plane did not wait for took the same risk): X-Plane's
  4096 px ring lies within a few hundred metres of 14 km (a texture at 8.7 km read at 4096 px and
  one at 14 km at 2048 px from LSGG, 2026-10-20; one at 13.9 km at 4096 px from a start in the
  air near LAX, 5,000 ft, counted in three dimensions, 13.8 km along the ground, 2026-11-11), past
  the rings' 13.6 km, and without the margin that texture near LAX was taken sharper than Live
  built it, its night black, the same in three flights. With it, in three starts, X-Plane went on
  after 13 to 14 s there, as in the four starts without (zooms 16/18: 10 textures at 4096 px
  instead of 9, 24 at 2048 px instead of 17, 270 MB downloaded instead of 254). At zooms 17/19 it
  makes a cold start longer: near LSGG by day, X-Plane went on after 54, 63 and 66 s with it
  against 40, 40, 41 and 41 s without, the two alternated (1.16 to 1.19 GB downloaded during the
  start against 1.05 to 1.07, 1.30 to 1.35 GB either way with the 30 s of path after: downloaded
  earlier, not more); with it but for the 8192 px level at a start, 45 and 51 s (1.10 GB)
  (2026-11-12). The start modes of before (`full`, `around`,
  `normal`, and `reload` until 27 September) went with the growing; a saved `start_mode` is left
  out. The pink test of 27 September (the 8192 px levels built pink, `tint_8192`) showed the pink
  at LFLL on a new load of the flight, never after `sim/operation/reload_scenery` (the SDK's
  `XPLMReloadScenery` is the same), which reloads the DSFs and keeps the textures X-Plane holds.
- **Imagery sources** are OrthoStudio XP's (`orthostudio.imagery.providers`: its registry and the
  user's `sources.toml`), listed as its Plan lists them, with Live's own between the two
  (`orthostudio/live/registry.toml`, the same grammar): AutoOrtho's FIREFLY and NAIP, measured on
  27 September. OrthoStudio XP's lists do not show them. AutoOrtho's GO2, YNDX and APPLE stay out,
  as GO2 stays out of OrthoStudio XP's registry (decision 0005). A country's source is completed by Bing
  Maps outside its bounds and wherever it answers "no imagery" (`orthostudio/live/providers.py`),
  and its pieces are kept under the pair's name (`SP+BI`). A source whose server has a ceiling
  (`server_req_per_s`) is never asked faster.

## One app with OrthoStudio XP

`orthostudio/live/app_start.py`, and the few lines OrthoStudio XP's own files gain for it: first on
the branch `live`, as a preview beside the usual app, then on `live-osxp`, the branch that ships Live
switched on (1.1, `ENABLED_BY_DEFAULT`; `enabled = false` in `live.toml` switches it off; the
suite keeps it off unless a test switches it on, `tests/conftest.py`):

- `desktop.main`, at the app's own start only (never a test's, which hands in its log and its
  doubles), asks `app_start` first: with Live switched on and `app_home` "ask" (the default), the
  app shows Live's window on its `/welcome` page; "live", Live's page; "build", or `OSXP_START=build`
  in the environment (Live's own button), OrthoStudio XP's window as ever. The welcome page's
  answer (`POST /api/live/home`) is kept unless its box is unticked; "build" opens OrthoStudio
  XP's window beside Live's at once, in its program, then closes Live's. A Live window open
  already is brought to the front (`POST /api/live/front`).
- **One window, the app's frame** (the user's choice, 2026-10-04, after a trial on a branch of its
  own: « Bien mieux que deux applications »; two windows that do not do the same thing and are one
  app were confusing). The two windows of before are kept behind `OSXP_FRAME=0`, and as they were
  on the branch `two-windows` of the private repo. Live switched on and a window possible,
  `app_start` shows the frame (`orthostudio.live.frame`) rather than the two windows, unless
  `OSXP_FRAME=0`; one up already in another program comes to the front (`osxp_window_listens`).
  The frame's page (`live/ui/frame`) is served by this program on the loopback, on the engine's
  port minus one, else a free one, with OrthoStudio XP's style sheet and scripts under `/static`.
  What it shows as OrthoStudio XP does (the name, the language, the theme, Quit, the text size,
  the zoom) it does not write: each is OrthoStudio XP's own element, taken from its page
  (`ui/index.html`) where `frame.html` marks it (`<!-- osxp: .topbar-tools -->`), as the frame's
  page is served; a change there reaches the frame by itself (a copy of Quit had stayed behind).
  It holds each part's page in an iframe, loaded the first time the part is shown
  (`FrameTools.ensure`: OrthoStudio XP's engine behind its opening page; Live set up in this
  program, `control.in_frame`, its welcome first when `app_home` is "ask"), kept alive after. A
  page in the frame (`ui/embed.js`, its origin the loopback's) hides what it leaves the frame
  (`data-frame-owned`: the top bar, the foot's text size and zoom), gives its screens as the
  frame's tabs, what it is doing (`busy`: "building", Live's state), its foot's height and the
  room its scroll bar takes on its right (the frame's foot stands left of it: its + lay over the
  scroll bar on Windows and on a Mac showing scroll bars always), and takes the language, the
  theme, the text size and the room the foot takes. The zoom is the
  window's (`FrameTools` is a `PageTools`), its keys typed in a page passed on to the frame. What
  the pilot chose is kept in `frame.json` and written into the frame's page as it is served; the
  first start takes, once, what the pages had kept of their own. Quitting (the frame's Quit,
  Cmd+Q, the app's menu and the Dock's, the close button on Windows and Linux) asks once while a
  build or a stream runs, in OrthoStudio XP's own dialog in the frame's page (`#quit-confirm`, put
  there from its page; `frame.js askQuit`, the words from `Frame.quit_words`, its Quit
  `Frame.quit_now`), as OrthoStudio XP's window asked its page before 1.1 (the user, 2026-10-09:
  not a box of the system's); a page that cannot answer (`window.ask_the_page`), in the system's
  box.
  The welcome's "build" and Live's Build tiles show OrthoStudio XP's part. A build while Live
  streams is asked about through a hook of OrthoStudio XP's page that names nothing
  (`window.osxpBeforeBuild`, filled by `osxp-entry.js`), Start and Prepare while a build runs
  through `GET /api/live/osxp-build`: questions, never refusals.
- **One program, two windows** (the user's choice, 2026-10-03: one icon in the Dock and the
  taskbar). The app's first window, either, runs the window library's loop (`window.show`); the
  other opens beside it in that loop (`window.open_another`), each by a name of its own
  (`window.PLACE_NAME` for OrthoStudio XP's, `web.TITLE` for Live's), every call naming the window
  it means. Live's Build tiles opens OrthoStudio XP's window there (`desktop.open_osxp_here`, its
  engine started behind the opening page as at the app's start); OrthoStudio XP's Live button,
  in its engine (a program of its own), asks Live's API to bring its window (`web.live_window_here`,
  which opens it again when it was closed) or, Live not set up yet, OrthoStudio XP's window
  process (`osxp_window_listens`, `live`), which sets Live up beside it the first time (its page
  and API in that program, `web._here`) and opens its window. The windows share the program's web
  view folder (`window.storage_dir`): each page keeps its zoom and text size by its address, and
  each window its place (`window.place_file`). Quit in a page closes that window alone, as a close
  button does on Windows and Linux (the user's choice, 2026-10-03: closing the whole app on one
  window's Quit was not what they expected), and the app goes with its last window. Cmd+Q
  (`web.app_may_quit`) closes both: at once when nothing runs, OrthoStudio XP's engine asked to
  stop; else one box over the window where a build or a stream runs, and on yes the build is
  stopped (`force`) or the stream without waiting for X-Plane (`LiveControl.quit(force=True)`,
  the plugin taking Live's links out at X-Plane's next start), and both windows close. Live set up
  beside stops with the app's last window (`web.end_here`). A program showing no window (the
  browser, a terminal) opens the other in a program of its own, as before.
- Live's window opens at once on OrthoStudio XP's opening page (`desktop.open_while_starting`,
  its `path` and `name` Live's), and Live is set up behind it in a thread (`web._set_up`: its
  code, its log, its control, its page and API), as OrthoStudio XP's window does with its engine:
  the window came only once Live was ready, after seconds of nothing on a cloud PC (a user,
  2026-10-03). Until then `web._Starting` stands for the control: nothing streams, the window may
  go. The app's `serve.log` says "Live's window opens: Python ..., loading the window ..." and "Live
  is ready: its code ..., setting up ..., its page ...". Live that cannot be set up closes its
  window (`LiveStartError`), and the app opens OrthoStudio XP's in a process of its own.
- OrthoStudio XP's engine includes `app_start.osxp_router`: `GET /api/live-app` (switched on, and
  Live's state, asked of its window's API), `POST /api/live-app/open` (Live's window brought to
  the front, else opened beside OrthoStudio XP's, else started as a program of its own:
  `front`, `beside`, `started`), and `GET /api/live-app/entry.js`, the button's
  script, which OrthoStudio XP's page loads with one line and which adds the button to the top bar.
- Live's window has Build tiles (`POST /api/live/osxp`: OrthoStudio XP's window beside it,
  `{"opened": "here"}`, else `python -m orthostudio.desktop` with `OSXP_START=build`). An
  OrthoStudio XP window of another program, put away or not, is brought to the front instead
  (`"front"`, else `"started"`): its process listens on 127.0.0.1, on a
  port the system picks, which `<osxp_home>/run/osxp-window-<engine port>.json` gives
  (`app_start.osxp_window_listens`, started by `desktop.in_a_window` with the window itself, the
  file removed when it closes). Each click used to open another window, one more app in the Dock
  (a user saw it, 2026-10-02). On Windows a window asked for this way goes in front through
  `winfront` (`window.to_the_front`), Windows letting only the program in front do it plainly.
- Where the other window cannot open beside, on a Mac each button opens it as a new instance of
  the app itself (`open -n -a
  <app> --env OSXP_START=live|build`, `app_start._other_window`), the app named by its launcher
  (`OSXP_APP`: the installed app's, the checkout's and the preview's, `tools/package`). Started
  aside, a Python's window was called Python in the Dock and carried Python's icon; opened by macOS
  from the app, the process is the app's (its name and bundle identifier in `lsappinfo`, tried
  2026-10-02). `OSXP_START=live` opens Live's page whatever was chosen, without the question.
  Elsewhere, or from a terminal, the Python is started aside as before.
- `desktop.ENGINE_PORT` and `LOG_NAME` follow `OSXP_PORT` and `OSXP_LOG_NAME`, which the preview's
  app sets (`tools/package/live_preview_app.py`: 8651, `serve-live-preview.log`): the usual app's
  engine takes 8641 and stops another OrthoStudio XP it finds there.
- Where no window can be shown (a Linux without the window's libraries, as OrthoStudio XP's page
  then), Live's page opens in the browser, and nothing closes with a tab. Started by the app
  (`ui --quit-when-closed`, `open_window(quit_when_closed=True)`), Live's process goes as
  OrthoStudio XP's engine does (`orthostudio/api/presence.py`): five minutes after its page last
  spoke, unless a stream is on its way, in or out, or a region is being installed
  (`LiveControl.busy`). The page speaks by asking for its status every second; the welcome page,
  which asks none, says it is open every 30 s (`POST /api/live/presence`). The app's icon or
  OrthoStudio XP's Live button then open the page again in the browser (`front`), and the
  welcome's "build" answer ends the process there too, for OrthoStudio XP's window.

## The pack

`pack` in Live's folder holds every AutoOrtho region installed (`Custom Scenery/z_autoortho/scenery/
z_ao_*` with a `terrain` folder, then those Live installed itself in its `regions` folder, a region
AutoOrtho has taking precedence), set up once by `uv run python -m orthostudio.live setup` or by
the first `stream`: every DSF (X-Plane lists them as it starts) and the files every square's
`.ter` may name (`water_transition.png`), in half a second for Europe (`region.setup_region`,
region version 3, 2026-09-29). The DSFs are clones of AutoOrtho's, file by file, else hard links;
on another disk than the region (an external disk, where neither crosses), a link to its folder:
X-Plane reads the mesh from there. A second region is merged in, a whole folder where the pack has
none of its name, file by file where it has (a DSF another region has already stays that region's);
merged into a link, the link first becomes a real folder of links, so that nothing is ever written
into another region's folder.

**The files the DSFs name, per square round the aircraft** (`orthostudio/live/names.py`). X-Plane
holds 4 x 3 squares round the aircraft (`evict.xplane_tiles`, measured, with its lag) and reads,
when it loads one, every `.ter` its DSF names, and the water masks and textures those name. Live
makes them for the squares of that window and a ring round it (`RING`): the `.ter` files and water
masks copied from the region (small files: a copy is the fastest on APFS and writes the least, 0.11
ms and 4.8 KB against 0.17 ms and 7 KB for a clone, 0.26 ms and 11.7 KB for a hard link), each
texture not built a name of one neutral texture (a hard link; a dull uniform colour at every
level). A thread decides which, a process of its own does the file operations (`names.run_ops`): in
Live's process they waited for Python's lock behind its other threads, a Norway start's 4.4 s
becoming 8.1 s with another thread busy half the time and 35 s at 90 %, against 4.7 to 5.3 s in a
process of their own (2026-09-30). A square whose files fail (a full disk, a file held open on
Windows) is tried again 10 s later, doubled each time, never stopping the others, and holds a
start at most 60 s (`GIVE_UP_S`, the log says so); an unexpected error stops Live, as the
builder's does. A texture recorded as built whose name had to be made again (its file taken away
by hand) is built again, unless the builder wrote its file over that new name meanwhile: at a
start the names are made while the first textures are built, and 168 good ones were built twice
over Norway (2026-09-30; `Names.leads_to_neutral`). A start's are
made from the click, and the plugin holds X-Plane until those of its window are there
(`Streamer.squares_ready`); in flight, the ring gives a square of flight to make the next column
before X-Plane loads it (4 to 7 minutes at 480 kt, 1.5 for a Concorde). The squares two rings from
X-Plane's window lose their files (`KEEP`), a texture built kept (the disk budget's), and none while
X-Plane loads scenery. Until 2026-09-29 the pack held every square's files from its setup: 400,000
textures and 684,000 `.ter` for Europe, four minutes to set up, three to lay out again for a new
zoom, and possible only on APFS, whose clones cost nothing. Measured then with Live's real names:

| | names | the Mac's SSD | a USB hard drive (Mac OS Extended) |
|---|---|---|---|
| a start at Frankfurt, the most in Europe: X-Plane's window and its ring | 12,906 textures | 2.7 s | 3.8 s |
| the same, with its 23,771 `.ter` files and masks | 36,677 files | 8.1 s (4.2 s for the window alone) | |
| the heaviest new column in flight | 1,680 textures | 0.4 s | 0.4 s, 0.3 s of processor, 1.5 MB written |

Checked against 80 of Europe's DSFs taken at random: the 30,540 `.ter` they name are all made with
their square (`evict.tiles_of`, a texture on a square's edge made for both).

Which files a square takes comes from the regions' textures (`names.RegionTextures`), each
region's listed from its `terrain` folder once and kept in a list of its own, in Live's folder
(`regions/.lists`, named after the region's folder and its whole path: `names.one_region`). A start
reads a region's list back while its `terrain` folder has not changed (its time, its identity), and
lists that region again otherwise: a region added later is the only one listed. A region is
listed before any start asks for it: by Live's install as it ends (the region it has just
unpacked), by the Windows setup for the regions already there (`python -m orthostudio.live lists`),
and as Live's window opens, in the background, for one installed meanwhile, AutoOrtho's
(`session.keep_region_lists`, `LiveControl.keep_region_lists_aside`). A start that comes while the
window's thread lists a region waits for that list and reads it, never listing the region a second
time (one lock a list). Listing Europe's 684,455 `.ter` takes 0.9 to 2.4 s on the Mac, and the
streamer's set-up that lists them took 57 s, then 19 s at the next start, on a Windows computer in
the cloud whose disk is slow for so many files (2026-10-03); read from its list, 0.07 s on the Mac.
The log says how long it took and which regions were listed. A region's list keeps its textures by
the square of their centre, with their zooms (list version 2): a start reads them so, and a
square's textures are worked out, where each one is, only the first time the streamer asks for
that square, as its files are made round the aircraft (`names.SquareTextures`). The world's
3,712,375 worked out at every start took 5.4 s of it; read by square, 0.71 s, and the 30 squares
round Geneva came in 8 ms, 7,970 textures (the user's Mac, 2026-10-31). A list of version 1 is
made again once: 9.0 s for the six regions of the world.

**The disk of Live's folder** (`orthostudio/live/filesystem.py`, `pack.check_folder`). A texture not
built is a name of the neutral texture, a hard link, which a disk allows up to a number of names a
file: 32,767 on APFS and Mac OS Extended, 1,023 on NTFS, 65,000 on ext4; past them, a copy of the
neutral texture takes the next ones (`names.NeutralPool`). Every file is written under a name of
its own beside it (`.live-tmp-<process>-<name>`), then renamed over its name (`filesystem.replace`):
two names of one file, a rename between them does nothing, so the first one is removed instead
(11,865 of them were left after one session, 2026-10-10); those a Live stopped in the middle of a
write left are removed at the next start, before anything writes there (`region.remove_temps`). A texture built keeps on the disk only
its levels built: its larger ones share the neutral texture's blocks where the disk clones files
(APFS, btrfs, xfs: `clone.clone`), else they are a hole of a sparse file (NTFS, ext4:
`texture.write_sparse`), which reads as zeros, black where the neutral colour would be, if X-Plane
reads a level before Live builds it. A disk that can do neither, Mac OS Extended, exFAT or FAT32,
would take every texture's full 11 or 45 MB round the aircraft, tens to hundreds of gigabytes:
Live refuses it for its folder, which the computer's own disk always can hold (APFS on a Mac, NTFS
on Windows, ext4 on Linux). X-Plane itself may be on any disk that takes the pack's link in its
Custom Scenery: a symbolic link, or on Windows, where one needs Developer Mode, a directory
junction (OrthoStudio XP's own, `install.packs`); not exFAT or FAT32, where no link goes. On
Windows a file X-Plane has open cannot be replaced: tried again for two seconds
(`filesystem.replace`), and every texture written in binary (`O_BINARY`). Every file Live replaces
goes through it, a test checks it: Live's window reads the regions' records every second and the
textures' record every 3 s, and a bare replace stopped a first install of Europe on Windows
(WinError 5, 2026-11-11). A save of the textures' record still refused after that (another
program holding it) is skipped and said, as a stop between two saves would leave it: the next one,
30 s later, writes it whole.

The pack records its regions (`sources` in `_live/region.json`): one installed since is merged in
at the next start, the textures built kept; one taken away sets the pack up again. A region
installed again or updated (its folders' times, `fingerprints`) sets the pack up again too, never
under a running X-Plane: DSFs of before beside `.ter` files of now must not meet. A region gone
(its `terrain` folder) stops Live from starting, where the pack once held every file its DSFs name
(2026-09-30). And a pack left in Custom Scenery by a Live that stopped without taking it out (a
crash, the computer restarted) is taken out by the plugin as X-Plane starts, when nothing listens
on Live's port: X-Plane starts its plugins before it lists its scenery (its Log.txt), so it never
reads a pack whose squares have no files. A pack of
before (region version 2) has its squares' files taken away once, the textures built kept, at the
first start with X-Plane not running (`region._to_squares`, a minute or two for Europe); while
X-Plane runs, it keeps every file and nothing is made or taken away. A pack that is there keeps
its layout, the textures added included, and a new zoom lays them all out again together.

## AutoOrtho's regions, installed by Live

**Live's own mesh first** (`scenery.osxp_regions`, 2026-10-18): Live's mesh is made ahead of time
with OrthoStudio XP's engine, from the relief of viewfinderpanoramas.org and OpenStreetMap; the
tools that make it are not in this repository, like those of the OSM library. A region of it
unpacked in Live's folder as
`regions/z_osxp_<id>`, its marker written once whole, comes before AutoOrtho's regions in
`default_sources`: a square it has takes its DSF (a DSF another region has already stays that
region's), AutoOrtho's serves the squares it has not.

`orthostudio/live/aoregions.py` installs a region the way AutoOrtho's installer does
(`autoortho/downloader.py`), from the GitHub releases of `kubilus1/autoortho-scenery`, which hold
the mesh of the whole planet (27 September 2026: na 25.8 GB, sa 9.2, eur 16.8, afr 12.3, asi 33.2,
aus_pac 6.2, 154 mesh zips, last released in 2023).
- **The list** is GitHub's API (`/releases`), newest release of each region first, pre-releases
  left out, as AutoOrtho reads it: a release is a region's when it has its `<id>_info.json`; its
  `z_<id>_NN.zip` are the mesh, its `y_<id>_overlays.zip.NN` the parts of one overlays zip, and a
  `.sha256` beside a zip (North and South America) its hash. Asked at most every hour, kept in
  `regions/catalog.json`, which stands in when GitHub does not answer. The window asks for it
  (`GET /api/live/regions`); its status only shows what is known.
- **The install** downloads each zip to `regions/.download/<id>`, resumed with a `Range` from
  what is on the disk (GitHub's storage serves `bytes=N-`, not the suffix form `bytes=-N`), each
  answer written as it comes, five tries a zip. A zip whole (its size; its SHA-256 where published;
  every member's CRC as it is read) is unpacked into `regions/.staging/<id>` while the next
  downloads, its top folder taken off as AutoOrtho does (`z_eur_15/`, and
  `y_<id>/yOrtho4XP_Overlays/` for the overlays, whose parts are read as the one file they make),
  then deleted; `state.json` records the zips unpacked, so a stopped install goes on from the next
  zip. Once all are unpacked, the mesh moves to `regions/z_ao_<id>`, the overlays to
  `regions/y_ao_<id>`, and the marker `.live-region.json` (release, date, source) is written last:
  a region counts as installed only with it. Another release on GitHub starts from scratch.
  Measured on `z_eur_05.zip` (0.47 GB): 20 s, 125 DSFs, 34,144 `.ter`, 1,111 masks, 784 MB on the
  disk; every DSF and `.ter` byte for byte as AutoOrtho installed them. 87 masks differ: a mask two
  zips hold is the last unpacked one's, in AutoOrtho as here.
- The disk must hold 1.7 times what is left to download, and 2 GB more. One install at a time.
  A region can be deleted, not while Live streams nor while it installs: Live's own folder, or
  one AutoOrtho installed as AutoOrtho's `Release.uninstall` deletes it (`z_autoortho/scenery/
  z_ao_<id>` and the record `z_autoortho/<id>_info.json`, its overlays left in
  `yAutoOrtho_Overlays`), and neither while AutoOrtho nor X-Plane runs. The window says
  "Installed" for both; who installed it shows on hovering.
- The repository names no licence: Live downloads from it as AutoOrtho does, and never publishes
  its files elsewhere.
- **No mesh here.** Which region has the mesh of each one-degree square is in
  `orthostudio/live/aoregions_map.json` (3.7 KB: 64,800 bytes, zlib, base64), made by
  `tools/live/aoregions_map.py` from the central directory of every mesh zip (154 zips, 604 MB read
  over HTTP ranges in 24 s; 20,992 squares, Europe's 1,981 as many as `z_ao_eur` has DSFs). Each
  second the streamer looks at the aircraft's square (the start's before X-Plane sends a
  position): where the pack has no texture within 100 km but a region has a mesh, it keeps a
  notice, {id, region, installed}, looked at again when the square changes. The window shows it
  in its flight panel; the plugin's `LIVE1 ASK` gets `LIVE1 NOTE id Live: no mesh here: install
  North America in the Live window` (or "North America is installed: used from Live's next
  start"), shown in a small panel with its cross, said once in Log.txt. A start there logs the same words. Over the open sea (no region), nothing.
- **The plugin's words follow the Live window's language** (`language` in Live's settings, set by
  the window's EN/FR selector through `POST /api/live/language`, or in the app's frame by the
  frame's own, through `Frame.keep` and `control.set_language`, read again at each answer while a
  stream runs): every answer of the gate says `lang=en` or `lang=fr`, and the plugin picks its own
  words by it (its loading window, the notice's panel and its cross's tooltip); the
  notice comes in that language, and a start's name too (`LFBZ piste 09`). Lines are UTF-8.
  Log.txt stays English, the notice excepted, which it says as shown.

## Roads, forests and buildings

X-Plane reads one ground per square, and its own scenery carries the square's roads, forests,
buildings and beaches: where Live's pack gives the ground, they are gone. AutoOrtho's overlays
(taken from X-Plane's scenery by Ortho4XP: `.net` roads, `.for` forests, `.agb` and `.ags` autogen,
`.fac` facades, `.bch` beaches) bring them back. At each start, while X-Plane is not running,
`orthostudio/live/overlays.py` brings Live's overlay pack up to date (`overlays` in Live's folder,
clones or hard links of the regions' overlay DSFs, or links to AutoOrtho's `yAutoOrtho_Overlays`
on another disk, copies on Windows): a square whose overlay is already there, the same file (a
link to it, or its size and date), is left alone, and only the others are placed or taken away
(the log says how many). Made again whole, the 16,621 overlays took 2.2 s of every start; brought
up to date, 0.34 s on a start like the one before (the user's Mac, 2026-10-31). The pack is
linked as `y_osxp_live_overlays` and listed right above Live's line at every start, wherever that
is (`session._overlays`). A square of Live's gets none:
- where the user's own tiles give the ground (they have their overlays);
- where another enabled overlay pack listed above Live's line has it (`y*overlays`: AutoOrtho's,
  Ortho4XP's, OrthoStudio XP's), which carries no exclusion zone: the same roads would be drawn
  twice. One listed under it is not read on Live's squares (`overlays.other_overlays` stops at
  Live's line): Build's first overlays with "Live everywhere" land there, and left Live's squares
  bare (the review of 2026-11-13);
- where an enabled pack of simHeaven X-World or its kin listed above Live's line has it
  (`orthostudio.install.xplane.SCENERY_OF_ITS_OWN`). Read in a user's packs on 27 September: its
  roads pack excludes `sim/exclude_net`, its scenery pack `sim/exclude_obj` and `sim/exclude_fac`,
  over the whole square; they would hide AutoOrtho's, but its manual says that X-Plane loads a
  pack even when exclusion zones hide it, and asks to disable what is not needed. X-World Europe
  covered 1,555 of the 1,981 squares of `z_ao_eur`; the 417 others (North Africa, Turkey...) get
  AutoOrtho's.
Global Forests carries `sim/exclude_for` over the whole square: listed above Live's overlays, it
hides their forests and keeps their roads and buildings, with nothing to do. The setting
`overlays` "off" leaves them all out. Its line is unchecked with Live's.

## Night lights

X-Plane 12's own ground glows at night where it is a town: its urban terrain files
(`terrain12/urban_*.ter`) name a night texture (`TEXTURE_LIT ../textures12/urban_FAR/*_LIT.dds`,
`LUMINANCE 600`, `LIT_FADE_PARAMS`), a generic block pattern laid every 2 to 3 km. A photo texture
replaces that ground, and without a night texture of its own a town on Live's ground is black but
for X-Plane's roads and buildings. AutoOrtho switches its ortho off at night instead (its Night
Exclusion, by the sun's height); XPME sells a night texture.

**The terrain files.** With `night` on (`LiveSettings.night`, on by default), each land `.ter` of
a square names its night texture right after its photo, `LIT_TEX_NOWRAP
../textures_night/<texture>_LIT.dds` (`names.with_night`, applied with the decal by `run_ops` to
the `ter` and `make` operations); the water's and the sea's files name none. The night textures'
names are made before the `.ter` files that name them (`("night", path)`), each a hard link to the
pack's black night texture (`_live/night.dds`, 1024 px BC1, `region.ensure_night_neutral`) and its
copies past a disk's names per file (`names.NightPool`), so a night texture not made takes no
space and adds nothing. A square taken away drops the names still black (`("drop_night", path)`);
one made stays, as a texture built does. A change of the setting makes the `.ter` files again at
the next start with X-Plane not running (`region.set_night`, recorded in `_live/night.json`, as
`set_decal`).

**What a night texture is** (`orthostudio.live.night`), as a prototype on the user's tile of Nice
settled it (2026-11-10): street lamps along X-Plane's own roads, one every 20 to 45 m by the road's
class (`ROADS`), a third white and the rest sodium orange, at random levels, one in eight out,
dimmer and farther apart along a `rural_edge` road; a soft glow round the lamps and the larger
streets; windows in a band 50 m wide along the town's streets. NASA's Black Marble 2016
brightness of the place (its density, 0 to 1) decides how many lamps are lit, a fifth where it
sees nothing and all from 0.12 (a village round Geneva; `LIT_SHARE_MIN`, `LIT_FULL`), a lamp
0.8 to 1.2 as bright (`LAMP_COUNTRY`), and the windows' light, 0.2 to 1.5 (`DENSITY_FLOOR`,
`DENSITY_SPAN`). The lights all times the density at first, a village's lamps came out a fifth
of a town's and the country went dark (the user at LSGG, 2026-11-11); against the same textures
lit so, Geneva's centre 1.44 of its light without a grid (1.65 before), the Pays de Gex's
villages 1.05 (0.89), the country south of Geneva 0.68 (0.48), the Jura's woods 0.13 (0.15).

**The day photo, tried and left out** (2026-11-11). Read at 28 m a pixel for what is built (its
texture where it is neither green nor water), it lit the blocks between the streets, a window or
yard in twenty pixels, and dimmed the lamps away from houses to a quarter. In flight the points
came out as soft blobs all over the fields round LSGG, which the photo's texture took for houses
(the user: « c'est exagéré les points partout »), and the dimming took 8 to 21 % of the towns'
light, the photo seeing too little built in a dense centre (Nice's centre 0.84, its hills 0.79,
Cagnes 0.87): Annecy came out darker. NASA's density does the country's part. Then
times the strength the setting `night_light` gives (`night.LIGHTS`: "soft" 0.55, "normal" 0.75,
"strong" 1.0; `LiveSettings.night_light`, "normal" by default, carried by `NightWork.gain` in
each job), what is fainter than 6 out of 255 black (`night.FLOOR`), and nothing else: a twentieth of
the day photo under the lights, as XPME does (its "night ground visibility", 5 % by default), came
out of BC1 as a checkerboard of purple, brown and green blocks, values of 4 to 10 out of 255 the
format cannot hold in their colour (BC1 holds red and blue in steps of 8; the user's LSGG,
2026-11-10). It is drawn at the same metres a pixel at every zoom, a ZL18 texture's at a quarter of
its side and enlarged: drawn at its own pixels, the airport's sharper zone came out twice darker
than the town round it. Its smaller levels are each the average of four pixels of the level above
in linear light, as the day textures' (`night.POWER` is `imaging.GAMMA`, 2.2): a texture shows the
same light from far and near (computed with the screen's 2.2; flown over Los Angeles, the user
found it better than either rule below, 2026-11-12). A power mean of 3 gave each smaller level about
15 % more light on the screen than the one above (computed: 1.16, 1.34 and 1.53 times the top's at
512, 256 and 128 px on three
ZL16 textures of Los Angeles; its stored values 30 % higher a level, a town 19.4 at 1024 px, 25.4
at 512, 33.2 at 256, 42.7 at 128), and the user saw the tiles dim one after another as the
aircraft came closer; the plain mean of the stored values, tried, made the far levels darker on
the screen, and the tiles grew brighter coming closer (2026-11-12). Seen from far or high up,
towns are fainter than with the power mean of 3: "Strong" (`night_light`) is the lever. `FLOOR`
holds at every level (the power mean of 3 of a faint glow gave 4 out of 255 there); in linear
light it takes more of a lone road's faint light at the smallest levels, far off (computed on
synthetic streets: 0.96 of it left at 128 px, 0.72 at 64 px, 0.03 at 32 px; a town 1.00; the
power mean of 3 lost under 1 %; a lone rural road where NASA sees nothing, 0.99, 0.40 and none,
against 1.01, 0.95 and 0.53 with the power mean of 3). BC1 itself still tinted
the dim edges of the glows: green kept in 6 bits, red and blue in 5, 2 % of a town's lit pixels
came out up to 8/255 greener than red (the review of 2026-11-11). Every light drawn being orange
or white, each block's two colours have their green and blue brought down to their red
(`night.warm_bc1`): no pixel tinted any more, at every strength and level, its error the same, a
0.1 to 0.3 % of light less; a higher `FLOOR` took the light without the tint (16: a fifth of a
town's light, the tint still there).

**The roads** are X-Plane's own (`orthostudio.dsf.networks`): the network chains of the square's
Global Scenery DSF, their subtype the `.net` file's `ROAD_TYPE` less its last two digits (10 to
29 primary, 30 to 49 secondary, 50 to 79 local, 100 to 119 highway, 120 to 129 ramps, a unit of 9
a rural edge; 151 to 153 railways and 220 power lines left out). On Nice's square they are 41,920
chains, 7,457 km, read in 0.4 s, against 9,261 km of OpenStreetMap roads of which 2,610 km of
service ways; flown side by side, night textures from either could not be told apart. They are on
every user's disk and are the same with simHeaven X-World or without; where the Global Scenery's
DSF is missing (a part of the world not installed), the overlays of Live's regions, cut from it.

**The density grids**: one per square, `<region>/night/<square>.png`, grey, 288 × 288 cells of 15
arc-seconds over the square and 0.1° round it, north-west corner at (lon − 0.1, lat + 1.1), cut
from Black Marble 2016's eight grey tiles by the world mesh's tools and installed with the region
(the user's choice, 2026-11-10), one zip a region (`night` in the manifest, Europe 11.6 MB); about
16 KB a square round Nice. A region installed with 2.0, before them, gets them by itself: each read of
the list of regions (the window's, at its opening and every ten minutes at most) starts their
download for the regions installed that lack them (`LiveControl._fetch_night_grids`,
`Installer(night_only=True)`, `osxpregions.night_to_fetch`), in the background, unseen on the page
(no bar, and the panel's Update counts no night zip), while Live streams too: X-Plane reads none
of them, and the night's process takes each grid as it comes. Only the grids' zip is fetched; the
region's marker records it, nothing else of the region changes, its set-up is not made again. The
pilot's own install or delete stops it first and the install brings the grids itself; a failure is
tried again at the next read. Without a grid a texture's lights
are ×0.85, what a town's grid gives on average. `OSXP_LIVE_NIGHT_GRIDS` names a folder looked in
first, for the test flights.

**When** (`orthostudio.live.nightwork`). Night, for Live, is from two hours before sunset to
sunrise (`NIGHT_AHEAD_H`, `nightwork.night_at`), by the sun's time at the aircraft: in flight,
X-Plane's day of the year and UTC time (`sim/time/local_date_days`, `sim/time/zulu_time_sec`, with
its other datarefs) and the aircraft's longitude; at a start, the time X-Plane saved with it
(`_time` of `Freeflight.prf`), its local time, its zone's, which may be up to two hours ahead of
the sun's (summer time; Nice's is Central Europe's: its towns lit at 17:50 local on 10 November),
so night at any of those hours (`START_UNSURE_H`), until the plugin gives X-Plane's own UTC time
as it loads the flight (`utc=` in its HOLD, `Gate.held_utc`, `Arrival.held_time`), which decides
there (X-Plane 12.4.4, flights started through its web API: Geneva at 17:30 on 21 June gave
15:30 UTC, at 06:00 on 20 December 05:00, 2026-11-11): with the doubt alone, a start at Geneva at 17:30 in June waited for its night textures in
daylight (the review of 2026-11-11). The caller of `NightWork.tick` gives the time that is right:
in flight X-Plane's own, at a start the plugin's or none, never the one X-Plane sends meanwhile,
the flight before's. The sun's height alone
(below 20°) made a winter's day in Europe night all day long, a start by day waiting for night
textures (the review of 2026-11-11). From then each
texture built gets its night texture (`NightWork.built`), and those built before get theirs at
once, the nearest first: X-Plane never reads again a level it took, and in the first flight with
Live, the night textures made after X-Plane had loaded their area, the textures X-Plane did not
come closer to stayed black (2026-11-10). A night texture is made from the same level down,
counted from the top of the texture's file as X-Plane counts it (`NightWork.want`: the night of a
2048 px file was a hole where X-Plane read it, the review of 2026-11-11): 1024 px for a file's
sharpest level whatever its size, halved at each level below, a quarter of the day texture's side
in a 4096 px file (a 256 px one far away a 64 px one), half in a 2048 px one; its larger levels a
hole, which reads black. X-Plane fades the night textures in by itself (at Nice on 11
November: nothing at 17:25, partly lit at 17:50, full at 18:15).

**One rule, in one place** (`NightWork.pump`, at each turn of the streamer's loop, X-Plane running
or not). It records what the processes made, sets how many processes there are (a team while a
start at night waits, as many as Live's encoders and 8 at most, `TEAM_MAX`; one in flight; none
by day), and gives two jobs a process, each the textures of one square up to four 1024 px
drawings' work (`JOB_WORK`: four near ones, or tens of far ones drawn small; jobs of four textures
whatever their size left the processes waiting between two turns of the loop, 3,157 of an LSGG
start's 8,535 made in 24 s against 8,530 so; the same replay with the code of before: 8,535), the start's
first (X-Plane's squares round it), then the near ones (`NEAR_LEVEL`, 1024 px, within about 40
km) nearest the aircraft first, then the far ones a square at a time, the nearest square first
(given nearest first, they went round the aircraft from square to square and each needed its
square's roads again: 3,951 reads for LSGG's 8,535 far textures, against 136). A pool of another
size takes no more jobs and is closed once its jobs are done, its memory with it. The jobs only
say they ended (`NightWork._end`): jobs given from their pool's own thread blocked it for good
when one of its processes died (Python 3.14, the review of 2026-11-11). A process lost closes its
pool, a new one made at the next turn, the textures given again; past five in a minute, no more
night for the flight (`LOST_MAX`), never Live stopped. A texture is never given while it is being
made (two at once wrote its file in either order, a coarse one last left it black); one that
fails is given again once, then left for the flight, the first five failures said in the log
(`TRIES`); one made neutral or coarser while it was made is not counted made, and is put back as
its day file once made, then made again from its new level if it is wanted (counted at the level
it was given, a 1024 px night stayed over a 256 px texture, the review of 2026-11-11). Put back as
its day file means read from the disk then (`NightWork._black`): black where the day file is,
which X-Plane may read, gone where it is not; told beforehand, a square made meanwhile was left
naming a night deleted. Filling the jobs stops once each is full and no room is left (28,800
textures wanted at nightfall went through the lock to the end each time). The night's
processes run at the encoders' lower priority, forked as they are (`pools.context`). Made in
each texture's own build job instead, as tried on 2026-11-10, the night took each of Live's
encoders (twelve on the user's Mac) 155 MB more (27 MB at rest, 182 MB once it had made one) and
Live 8.9 GB in flight against 4.7 at most without the night.

**Its memory**, measured on the user's Mac (2026-11-10), 18 MB at rest and 110 to 140 MB at work:
- 34 MB of modules: numpy and Pillow, no scipy (`night._blur`, `_max2` and `_zoom_linear` give
  scipy's values within 1e-6, faster) and not OrthoStudio XP's DSF package, which only the roads'
  own process loads;
- each square's roads read once, in a Python of its own that ends with it (`nightwork._read_apart`,
  a second more a square): a DSF read in the night's process left it 70 to 120 MB more for good;
  straight into arrays (`night.roads_of`, 64 MB at most on Marseille's square against 138 with an
  object a road), their points float32 degrees from the square's corner, a centimetre anywhere,
  2 to 6 MB a square; the last nine squares kept (`_ROADS_KEPT`), a texture on several drawn from
  each in turn, never copied into one;
- 60 to 70 MB the drawing keeps for the next texture: NASA's density worked out at 64 points a
  side and drawn up between them (`night.DENSITY_SAMPLES`; at every pixel it cost 120 MB a
  texture by itself), the arrays added in place, the photo and the first reduced level a channel
  at a time.

The start's team, replayed on an LSGG-like cold start (below) and measured as Activity Monitor
counts it (`phys_footprint`, 2026-11-11): its 8 processes 2.6 to 3.8 GB at most together from
one replay to the next, 400 to 520 MB the largest, with the roads read cold (the team reads the
DSFs itself) as from their files, the code of the morning before the reviews alike; Live's own
process 35 MB. For the seconds of the start only: the team ends
with it, or 180 s after its photos were built.

A night texture written after X-Plane loaded its area is read as X-Plane comes closer, as the
photo's levels are: black placeholders replaced 20 s into a path were shown equal to a reference
by 100 s, with no reload.

**A start.** X-Plane gives no time before it runs: night at a start is worked out from the time
it saved with it (above). A start at night waits for the night textures of the textures built in
X-Plane's squares round it (`evict.xplane_tiles`), which X-Plane reads there, as for its photos,
at the plugin's HOLD and in Live's pause window, counted with the day ones in its window; once the
day ones are built, those wanted then (`_start_set`: those built after, farther, are made in
flight, 27 s more at LSGG), for 180 s at most (`NIGHT_WAIT_S`). It waited for every texture of
the pack, other regions' too: 11,142 night textures at a Paris start, Nice's and the Alps' among
them (the review of 2026-11-11). The team works from the first texture built at a start at
night, while its photos are still built and X-Plane starts, and ends once the start waits for
none (`NightWork.waiting`, set by `start_left`: its photos or its night textures still to make),
or with the start (the streamer's `arrived`: X-Plane flies there, or went elsewhere). Kept until
X-Plane flew there, it held its memory while X-Plane loaded once its night textures were made,
and for good after a Prepare never flown (the review of 2026-11-11). Each square's roads are kept in
the pack once read (`_live/roads/`, `nightwork.roads_file`, 4.8 MB a square round LSGG, the oldest
dropped past Live's share for them), one process reading a square while the others wait for its
file, 30 s at most from the lock's own time (`_lock_square`: one left by a process that died is
not waited for); only a read that worked is kept on the disk (a passing failure, the memory
short, was kept as a square without roads, dark for good), and a DSF that could not be read is
read again at the next job, its job's other textures failing at once (kept for the process's
life, the flight's one process left the square dark for the flight). The team's processes read the DSFs themselves, as they
end with the start; the one process in flight reads each in a Python of its own. Replayed on an
LSGG-like cold start (8,535 night textures over 62 squares, the photos built over 21 s while
X-Plane starts, X-Plane asking at 24 s): every night texture made by then, against 79 s of wait
in the user's flight of 2026-11-11. The steps: one process, 13 minutes; a team once the photos
were built, 64 s; the roads and lamps laid out at once rather than a road at a time, 38 s; the
roads in files, 27 s; the team from the first texture, 7 s; the near ones to it too, 0 s.

**The disk.** Live stays within its disk space, each file counted as it holds (`evict.usage`): a
night texture made with lights, at the level it came back from (`NightWork.lit`, gone once put
back black with no job running; a night without a light is the black name, which takes
nothing), and one being made, at the sharper of the two, which may be written whatever happens
to Live (`NightWork.counted`, kept in the textures' record as `lit`; a job that fails writes
nothing, and noted when given, one that failed stayed counted for good), each counted by itself,
its texture built or stale (a season's change left them all out, counted with their built
texture); and an 8192 px file's copy of its 4096 px level from the moment it is given
(`Streamer.copied_up`, kept as `copied`, 33 MB more a file), counted while the file is built
from that level, read back only while the record is the one saved with it (`pack.copies_in`: an
older Live rewrites the record, and the file). A Live stopped while nights or copies were made,
Windows refusing to put one back black, a night made coarser while a job wrote it sharper are
counted; a reset puts back black every night the record counts. Short only by what a crash loses
since the record's last save (every 30 s), and, after a season's change, by the textures waiting
to be built again in the new one, their old files counted once built again or made neutral
(minutes; as before the night) (the reviews of 2026-11-11). Not measured: on Windows, at night,
whether Live's smallest space (10 GB) holds the textures it may not take away round the
aircraft, a far night there taking as much as its photo. A change of the night lights between on and off forgets `lit`
(`region.set_night`). Looked up by name in sets and dictionaries: the record's lists made the
Settings' figure 2.5 s for 28,800 night textures, every 3 s. Counted for every texture, the night took half of Windows' space by
day before any was made, and a copy went uncounted (the review of 2026-11-11). Live stopped, the
Settings' figure is counted from the same record (`control._textures_bytes`). A texture made
coarser, or set to a new zoom's size as its square is made (`Streamer._refit`), has its night made
black, then again from its new level when it is night (`Streamer._night_follows`; kept sharp, a
night texture took fifteen times its day one; left at its old size, X-Plane read a hole, black,
until the next night); one made neutral (the disk budget, a season changed: `Streamer._neutral`)
has it black again, or gone with it; a night texture without a light (the sea, a forest) is the
black name, not 684 KB of zeros. The roads kept for them take 5 % of Live's disk space, 1 GB at
most (`streamer.ROADS_SHARE`, `nightwork.ROADS_KEPT_MB`: a fixed share of a 200 GB space set
10 GB aside for 1 GB of roads), the night's loop dropping the oldest once a minute
(`nightwork.prune_roads`). Every texture file, day and night, is counted as the disk takes it
(`evict.file_bytes`): its header and its levels written, each in the disk's steps
(`evict.ALLOC_BLOCK`), 4 KB on APFS and ext4, 64 KB on NTFS, which gives a sparse file's data
64 KB at a time. Checked against the free space on the user's Shadow (2026-11-11): 2,000 far
night textures took 132,528 bytes each, counted 131,072 (6,840 before), 2,000 of 512 px 259,314,
counted 262,144; NTFS's own list of a file's parts stops at its end, short of its last step (the
113 KB a night texture said before). Counted by their levels alone, the photos too took up to
nearly three times what was counted on Windows (a far one 131,072 bytes, counted 47,800), and Live went past its space there. On the textures of an
LSGG start (8,535, most far): photos 0.75 GB and night textures 0.09 GB on a Mac (11 %), 1.45
and 1.13 GB on Windows (44 %). The night textures of every texture built are made
again at each start of Live as night comes. Turned off ("No"), the night textures, their black one
and its copies and the roads go (`region._drop_night`), and the night is made only when the
pack's terrain files name it (`region.night_on`: turned on while X-Plane ran, they do from the
next start); the terrain files and the night's work follow the pack's record, not the settings
(turned off while X-Plane ran, then on again, the squares flown meanwhile were made with no night
line for good; turned off, the night textures made before were neither counted nor made black
with their day texture): the settings say only whether more are made (`NightWork(lights=)`). Deleting
the textures deletes the roads too; a start removes the half-written files of their folders and
the locks a process that died left (`region.remove_temps`). A start's count, night textures with
the day ones, is worked out in one place (`streamer.start_count`), X-Plane held or not: as it is
for the plugin and the pause window, as it was at most a second before for Live's window
(`COUNT_EVERY_S`, 7 ms to count 8,000 textures); the start is ready, for all of them, when none
is left. The window said 0 textures to build at 100 % while the plugin counted the night ones,
then ready, or nothing, while X-Plane waited for them, and its count stood still once X-Plane was
let go (the reviews of 2026-11-11). Its three minutes for the night textures run once its photos
are built whether X-Plane is held or not ("Prepare my departure"), the team then ending too.

**Far textures drawn small.** A night texture is drawn at four times its own side
(`nightwork.FAR_DRAW`, 128 px at least) and made smaller, not at 1024 px: a 64 px one far away
at 256. Drawn k times coarser than a ZL16 texture at 1024 px, a lamp is a pixel, its light
(4/k²)^(1/2.2) of its level, which keeps its light on the screen, and (4/k²)^(1-1/2.2) of it in
the glow, which keeps the lamps' part of the glow in stored values (the glow, drawn wider, keeps
less of it on the screen, and the streets less still: 0.59 and 0.43 of their light at 128 px on a
grid of Nice, computed, 2026-11-12); a street and the windows' band keep the width they had at
1024 px (`night._width_k`). Against the same textures drawn at 1024 px, round Geneva, Nice and in
the country (ZL16): 0.87 to 0.99 of the light on the screen on average (0.85 to 1.08 a texture),
0.87 to 0.99 seen from far; round LSGG (ZL18) 0.73 at 128 px and 0.87 at 64 px, such a texture
gaining up to 37 % more light (1/0.73) when Live builds it sharp (computed with power 2.2,
2026-11-12; 0.69 and 0.87 on the test's grid of streets at ZL18; with the
power mean of 3 on stored values, 0.89 to 1.02 and 0.83 to 1.00). Drawn in 37 to 75 ms against
99 to 131 (2026-11-11; the roads' loop is most of what is left).

**Measured** with the prototype's textures on the user's tile of Nice, X-Plane alone, 2704 × 1756,
three flights each way alternated: frame times at 21.1 against 21.7 ms over the city, the same on
the approach and from far, X-Plane's memory and its first load the same; 54 MB of night textures
for 1.1 GB of photos; 0.15 to 0.25 s a texture in the module. On a Windows PC (16 GB, the
Shadow), two pairs of night flights at Nice with Live's own night textures, the lights on and off:
0.6 to 0.9 ms more a frame with them (3 to 5 %), 0.9 to 1.0 ms at the 99th percentile, Live 0.6 of
a core more in flight, 0.52 of it the night's process (2026-11-11; the Mac: frame times the same,
0.06 of a core more).

## The user's own tiles

For each 1-degree square, X-Plane draws the ground of the first pack of `scenery_packs.ini` holding
a DSF of that square that is not an overlay (`sim/overlay 1`). Live's line goes under the tiles
OrthoStudio XP built or imported from Ortho4XP, unless the setting below or the pilot put it above
them ("Live's line in the list"). At each start, after its line is listed, Live reads the packs above it
(`orthostudio/live/covered.py`): an enabled pack with a DSF of a square that is not an overlay
(its HEAD's PROP atom, unpacked from its 7z archive when it is one; OrthoStudio XP's and Ortho4XP's
tiles without reading) covers that square. The planner leaves alone every texture all of whose
squares are covered: nothing downloaded, nothing waited for at a start. A texture that reaches
into a square Live draws is built, for that square.

Live's own settings choose (`own_tiles`, Settings, "Your own tiles"): "first" (the default) lists
Live's line under the pilot's tiles; "under", Live everywhere, right above the first of them, and
none covers a square any more: Live builds its textures there too, with no step in the relief at
the tiles' edges (OrthoStudio XP's tiles stand on X-Plane's relief, Live's mesh on
viewfinderpanoramas'). The setting moves the line once, as it is saved (`scenery.move_live`, the
API's `PUT /api/live/settings`); at its stop Live's line is unchecked and the tiles are back. The
window's map and checks show the squares with Live's line where it is listed, else where Live's
next start lists it (`covered.own_squares`, the same rule, in memory).

### Live's line in the list

One rule, in `scenery.place_line` (the user, 2026-11-13: « l'utilisateur doit pouvoir changer de
place sans que live n'écrase à chaque fois la valeur »):

- **Missing**, Live lists its line right above the first mesh after `*GLOBAL_AIRPORTS*`
  (`scenery.live_anchor`), so under the pilot's tiles, every other photo pack and everything that
  draws over the ground; with "Live everywhere", right above the first of the pilot's tiles
  (OrthoStudio XP's, Ortho4XP's) when it comes before the first mesh (one listed under the mesh
  is hidden by it, and Live would be too). No mesh: the end of the list. A mesh
  (`SceneryPacks.is_mesh`, Build's rule for a first tile too): AutoOrtho's or XPME's by their
  names (mounts, never read through), or a pack named as meshes are (`zzz_`, `zz_`, any `z` name
  sorting after the tiles') whose first DSF is ground (no `sim/overlay 1`); its name decides when
  that DSF cannot be read (a disk unplugged). A library named `z...` (no
  DSF) took Live's line above every simHeaven X-World pack by the name alone, and Live's photos
  hid their buildings (issue 24).
- **Listed**, wherever the pilot or a sorting tool (xOrganizer) put it, it is never moved: checked
  at a start (`scenery.list_live`), unchecked at a stop (`scenery.unlist_live`), its link kept,
  so that X-Plane keeps the line there (it drops the line of a folder it cannot reach). The
  window's "Take the pack out of X-Plane" and a change of X-Plane folder do the same as a stop.
  The plugin stays in X-Plane: started without Live, it takes Live's links out only when Live's
  line is checked or gone (see "The plugin").
- **The setting own_tiles changed**: moved once, as above (`scenery.move_live`).
- **A start that fails** once the line is listed (a port taken, a disk full) unchecks the line it
  checked, as a stop does; one checked before it (another Live streaming, a running X-Plane it
  rejoined) stays as it was (`session.prepare`).
- **A start without Live's port** (`--no-plugin`, or a plugin that cannot be installed) takes an
  earlier plugin out of an X-Plane not running: finding nobody on the port as X-Plane starts, it
  would take Live's links out while Live streams.
- **Live out of X-Plane for good** (`python -m orthostudio.live uninstall`, which the Windows
  uninstaller runs: `session.remove`): its lines, links and plugin, in the X-Plane named in the
  Settings, nothing while X-Plane runs. Another X-Plane Live used keeps them, the plugin idle.

Build lists a new tile right after the last of its tiles (spec install 3.2), so on the side of
Live's line the setting chose, with no rule of its own about Live. Its first tile ever goes right
above the first mesh by the same rule (`SceneryPacks.is_mesh`), which Live's line is (its DSFs are
ground): above Live's line, with "Live everywhere" too, and every tile after it follows it there; changing the setting twice,
or moving the line, puts Live above them (the guide says so). Live's overlays line goes right
above Live's at every start (`session._overlays`). An X-Plane started while Live's line is still
checked (Live crashed, was left installed, or quit during a flight), or after X-Plane dropped
Live's lines with its folder out of reach and would list them again checked at the end, finds the
plugin, which removes Live's links (`take_out_if_alone`): X-Plane drops their lines, and Live's
next start lists its line again as missing. Until 2026-11-13 Live took its line out at every stop
and listed it again by Build's rule at every start and at every change of the list between two
flights: moved below simHeaven by hand, it came back above within seconds.

While Live streams, every 5 s (`streamer.BETWEEN_S`), it looks at the time of
`scenery_packs.ini`: once it changed and X-Plane is not running (X-Plane reads the list as it
starts), the same steps as at a start run again (`session.own_ground`, the overlays: Live's line
never moved), and the streamer is told the squares it leaves alone (`Streamer.set_covered`): a
tile taken out while Live ran was left alone until Live's next start (Haneda, 2026-10-18). Live's
own lines written then are not taken for a change.

## The loop

- **Every 2 s** the planner reads the aircraft's position, track and ground speed (UDP; from its
  last two positions when X-Plane says no speed while it moves, a position set from outside such as
  the measurement flight's drone: `motion_from_positions`, 2026-09-29) and looks at the path 90
  seconds ahead, straight on its track (5 minutes until 2026-09-28: in a turn that line
  swept all around the aircraft, and Live built 233 textures at 8192 px where X-Plane read 133,
  the encoding costing a few frames; 90 s still leave about two minutes before X-Plane reads each
  level, at any speed); it reads the camera too (`view_x/y/z` against the aircraft's
  `local_x/y/z`, both in X-Plane's OpenGL frame), so that the disk's budget keeps what X-Plane may
  show round it (`Streamer.may_coarsen`, `far`): X-Plane takes each texture's levels for the
  aircraft alone (`live/levels-test.md`). For every texture within 300 km: the level X-Plane
  will read of it at its closest approach to that path (`orthostudio/live/rings.py`), at least
  256 px. Textures built coarser are
  queued, the most urgent first: urgency is the distance left before the ring where X-Plane reads
  that level (230 km for 256 px, where X-Plane loads a tile). A texture built coarser than X-Plane
  reads it from where the aircraft is now is urgent whatever sharper level its path asks: one 16 km
  ahead, queued at 4096 px for the path and not urgent, was read at 2048 px 3 s after a texture
  quality raised let X-Plane go, 2 s after Live wrote it (2026-11-13).
- **The builder** takes the most urgent textures while fewer than 1,024 pieces are awaited and it
  holds fewer than 8,192 pieces in all (`HELD_PIECES`); past that, only textures X-Plane may be
  showing already. The encoders take the most urgent ready texture as the planner's last pass rates
  it (`in_flight`), not as when it was taken. Until 2026-09-30 the builder counted textures, and
  those that could not be encoded while X-Plane loaded scenery filled it: urgent ones waited behind
  them (the Norway flights of 2026-09-29: 49 and 103 late 8192 px levels given their copy). The
  status line says how many textures wait for an encoder. A texture
  takes its pieces from the cache, asks the downloader for the others, then for the parents of
  pieces without imagery (never coarser than ZL13), and goes to the encoding pool as soon as it
  has them all; the file is
  written beside the old one and `rename()`d over it. X-Plane opens a texture again for every
  sharper level, so it reads the new file at its next ring. A sharper build starts from a clone
  of the file itself and writes only the levels above the one it had (`write_sparse`'s
  `keep_from`): those below stay built from their own photos, as AutoOrtho builds each level,
  instead of being written again from the sharper ones. **No photo coarser than ZL13**, whatever
  the source (`texture.MIN_PHOTO_ZL`): a level coarser than that (a ZL16 texture's 256 px one) is
  made of ZL13's pieces, shrunk (four instead of one ZL12 piece, 80 kB of photos instead of 29).
  Bing's ZL12 and coarser come from another, lighter and yellower imagery than its ZL13 and finer:
  a 256 px tile's mean colour 21 to 31 apart from ZL14's at seven places of the world, ZL13's about
  2 (2026-10-19), and seen from cruise over the Swiss plateau the textures made sharper as the
  aircraft passed stood out as darker rectangles among the far ones. Textures recorded before the
  floor (the pack's record without `photo_floor`) whose photos were coarser are built again as
  X-Plane comes. Three sources change imagery below ZL14 instead, and say so in their entry of
  the registry (`min_photo_zl = 14`, `texture.photo_floor`): Esri Clarity (its ZL13 21 apart from
  ZL16 over Bern), Spain's PNOA (19 over Madrid) and Japan's GSI (31 over Tokyo); their far
  textures take sixteen ZL14 pieces (Bing's would be 331 kB of photos, not 80). Esri, Esri Firefly,
  EOX, Luxembourg's, PDOK, USGS and NAIP agree with themselves from ZL13 (from ZL12 most of them). Where the disk clones nothing, every level
  is written again.
- **While X-Plane loads scenery in flight** (`sim/graphics/scenery/async_scenery_load_in_progress`)
  and 5 s after, Live pauses (`Streamer.x_plane_loads`): nothing downloaded, encoded, copied up or
  made coarser, only the squares' files X-Plane needs made (their release waits too). Over Norway,
  Live's work during X-Plane's loads delayed the third tile of a column by 1 to 7.5 minutes in each
  of five flights (17/19 and 16/18); paused, none came more than 24 s after the first, near X-Plane
  alone's 19 s, with fewer 8192 px levels late (49 against 81) and X-Plane's cores the same
  (2026-09-30). Until then Live held back only its encoding, for 30 s, after the mountain test
  pair's hitches as X-Plane loaded a new row of DSFs (Live 146 and 387 ms there while it encoded and
  evicted, AutoOrtho none over 40 ms, 2026-09-28). At most 120 s of each load, so that one X-Plane
  would not end for another reason holds Live no longer, counted again from each move of X-Plane's
  window while its flag stays up (`evict.XPlaneWindow`: its columns 0.15 degree past a column's
  middle, its rows 0.1 degree past a row's edge, in the direction of travel, as measured): one flag
  of 519 s over three columns had Live back at work after 120 s, and the second column's last tile
  came after 237 s (2026-09-30). The flight paused for whole loads had followed three hours of rest,
  the others minutes: the Mac's heat before a flight foretells X-Plane's overload as well, so the
  pause's own share is not known yet. X-Plane loads at most two tiles at once and begins the third
  once one of them is done, its 7 background workers busy all along (the analysis of 2026-10-01,
  `build/flights/analyse-blocages-2026-10-01`). Never at a start, where X-Plane waits for what Live
  builds. The log says when a pause passes 120 s, and at a load's end how long it took and whether
  Live paused all of it. Starting nothing new from 15 s before the aircraft reaches the line where
  X-Plane moves its window (its columns 0.15 degree past a column's middle, its rows 0.1 degree past
  a row's edge), what runs left to end, showed no gain over a flight (the user's idea: columns 14,
  10 and 9 in 115, 130 and 107 s, against 74, 110 and 75 s paused during the loads alone; the same
  column took 17 to 115 s from one flight to another, 2026-09-30): not kept.
- **X-Plane's background workers** (`machine.xplane_pool`), read every second in flight from
  outside, without privilege (macOS's libproc, Linux's `/proc`, Windows' NtQuerySystemInformation
  with GetThreadDescription; Windows not tried with X-Plane, which may name no thread so there, and
  then Live goes without): when on average over 5 s all but one of its "Pool worker (background)"
  threads run, Live stops all but what X-Plane shows now: nothing downloaded, encoded or made
  coarser ahead of time (`Streamer.x_plane_pool`, `Streamer.holds`: the downloader puts a piece
  ahead back in its place, one X-Plane shows passes it). It starts again once two and a half of them
  on average are idle, 10 s at least after it stopped, so that X-Plane catches up. X-Plane loads a
  column only once its workers have done what they were doing, placing trees above all (X-Plane's
  stacks during its waits, 2026-10-01: 80 % of their time, 57 % in the geoid's height under each
  tree). On a hot Mac with Live at work they were all busy 61 % of a flight and before each load (6
  to 6.9 of 7 running), its tiles 44 to 135 s after the loads began; with Live idle, 16 % and 3.1 to
  4.5 of 7, 16 to 85 s; X-Plane alone 13 %. Live only holding back what was ahead, its downloads and
  its main process going on (0.28 core), left them all busy; its encoders in the system's background
  band, on the efficiency cores, only took longer there.
- **The encoding processes** are forked by a fork server (`orthostudio/live/pools.py`): a bare
  Python that Live's process starts first, before OrthoStudio XP's engine is loaded. The engine
  keeps PROJ's database open, PROJ closes it in every process started from Live's, and macOS's
  log crashed one there (2026-09-28, the first 8192 px texture of a flight from LFBO): its pool was
  broken for good, the builder stopped, and Live with it. A pool that loses a process all the same
  (a crash, macOS short of memory) starts again: the textures it was encoding fail and are queued
  again. More than 5 pools lost within a minute stop the builder, as before.
- **This computer's share** (`orthostudio/live/machine.py`; macOS, Linux and Windows alike): the
  encoding processes are every core but two, all at work while X-Plane waits on a start, and one
  for each 3 GB of memory at most: an encoder takes up to 0.36 GB at a 4096 px texture (twelve of
  them 4.6 GB at a start, 2026-09-28). While X-Plane flies, a third of those cores encode at once,
  four at the very most (a Mac with 14 cores flew without a stutter with four; a computer with 8
  gets two), and one for each 8 GB. An 8192 px texture takes 1.3 GB while it is encoded: one at a
  time for each 12 GB, four at most. A computer of 16 GB: five encoders, two at once in flight,
  one 8192 px. ZL17 and ZL19 want 32 GB. On the mountain test path, X-Plane and Live together took
  about 22 GB on a Mac, where the textures share the memory; a PC's graphics card holds them
  apart, which is not measured. On a smaller computer the log and the window say so, in the
  checks and under the zooms; the choice stays the pilot's. The memory is read with `sysconf`
  (macOS, Linux) or `GlobalMemoryStatusEx` (Windows), not through OrthoStudio XP's own reading,
  whose module loads PROJ before the fork server starts. `OSXP_LIVE_MACHINE="8,16"` gives Live the
  share of a computer with 8 cores and 16 GB, to try it on a larger one: X-Plane keeps the whole
  computer.
- **Short of memory in flight** (`machine.memory_pressure`, read every second without a process
  started: macOS's own level, Linux's memory available, Windows's memory load): short of it, Live
  encodes one texture at a time and no 8192 px one ahead of time; very short, only what X-Plane
  reads now (`Streamer.may_encode`); logged. On a simulated 16 GB computer (48 GB less 26 wired,
  the pilot zooming far out and back), Live's bursts of 1.5 GB and 5 cores had macOS compress and
  swap while X-Plane stalled up to 370 ms (2026-09-28).
- **The downloader** (`orthostudio/live/downloader.py`) keeps one HTTP/2 session for the whole run and serves
  pieces one by one from a priority queue, the most urgent first: a slow piece delays only its
  texture (the first version waited for whole batches and once stalled 40 s behind one piece).
  Measured against Bing: 2,000 pieces in 4.3 s, no gap between two answers over 0.81 s. A piece
  not there after 2 s is asked of another of Bing's servers as well, and the first good answer
  wins. Before that, two stuck pieces held a start at LFMN 30 s, and one of them never came.
  After four tries (up to about 40 s) a piece has failed, and its texture is built without it,
  from the zoom below, as where there is no imagery.
- **Dead connections**: requests on their way and no answer at all for 20 s (`DEAD_S`, looked
  at every 2 s), the session is replaced by a new one, with new connections, and the old one
  closed: its requests fail and are asked again on the new one; the log says so once a minute.
  Over Calabria on 2026-10-19, after half an hour of a line choked by an upload, Bing's four
  HTTP/2 connections stayed open and acknowledged what Live sent, but answered nothing (0 bytes
  received in 10 s, 5 KB sent), and nine it had closed were never closed on Live's side; curl
  kept asking on them, each request failing after its 8 s, and nothing came for the rest of the
  flight, when a new connection was answered at once (0.13 s). A quiet time, nothing asked, is
  never taken for one: the 20 s count from the first request after it.
- **A source that refuses this computer**: its server answering 403 to 32 requests in a row
  (`REFUSED_IN_A_ROW`), with no other answer between them, refuses this computer's address
  (`Downloader.refusing`), as Esri's edge (Akamai) answered « Access Denied » to a user's after
  heavy use (2026-10-07). A 403 now and then among its images is a piece it lacks. The log says
  so once; while Live runs, the window says it under the source, in OrthoStudio XP's words for
  its map with what to do (« Esri World Imagery refuse votre connexion (accès refusé) : arrêtez
  Live et choisissez une autre source en attendant »), and the textures' label reads « Downloads
  refused », until the server answers otherwise. A start that waited a minute for nothing names
  the source too, where it asked whether the internet was there.
- **A piece that does not come**: a texture X-Plane waits for (urgency 0 or less) waits no more
  than 10 s for a piece (`STUCK_S`); then it is built without it, from the zoom below (one piece
  Bing never sent held a start at LSGG 20 s, 27 September). The downloader goes on, and what it
  brings is cached. A texture built without some pieces, given up or failed, is built again from
  the cache and the downloader after a minute, then after twice as long each time they still do
  not come, up to 30 minutes (`patchy` in `_live/ortho.json`, kept from one run to the next); that
  build is never what X-Plane waits for.
- **The flight plan**: with the pilot's SimBrief name (`simbrief_user`, empty by default), Live
  asks simbrief.com for their last plan when a flight starts or X-Plane is found flying
  somewhere new (never at Live's own start: nothing says the pilot will fly it), at most once a
  minute; and, for the window's "Prepare my departure before starting the flight" box, while the
  window is open: when it opens, when the pilot comes back to it and every two minutes, at most
  every 30 s (`LiveControl.departure_suggestion`, `GET /api/live/departure?fresh=true`); at once
  with the arrow after the plan's line, at most every 5 s (`now=true`, `live.js checkDeparture`: a
  word when no newer plan came, the user asked 2026-10-03). A plan is known by
  its briefing (SimBrief's `request_id`, else `time_generated`): another one is offered ("Flight
  plan updated", "Load it"), never put in a box the pilot may have typed in (`orthostudio/live/flightplan.py`, through OrthoStudio XP's own reading of a briefing:
  its airports and its navigation log's fixes). While the aircraft is within 15 km of the route
  and, moving faster than 25 m/s, flying its way (within 100°), the path ahead is the track's two
  minutes and the route's as far, whichever passes nearer a texture; otherwise the track's alone.
  The route's ten minutes until 2026-10-12 had nothing measured behind them, where the track's two
  minutes late none on a flight of tight turns, and downloaded ten minutes of route for nothing
  when the pilot left it.
- **X-Plane's image**: every ring but the 256 px one grows with X-Plane's image
  (`sim/graphics/view/window_width` and `field_of_view_deg`, read with the position): the rings
  were measured on an image 2704 px wide with a 65° field of view, and
  [probe flight 2](live/probe-flight-2.md) found
  X-Plane reading each level farther on a wider image and with a narrower field of view, as the
  ground a pixel covers shrinks (`rings.screen_scale`: the width's ratio times the ratio of the
  half fields of view's tangents, never under 1, at most 4). The last image is kept in
  `_live/ortho.json` for the next start, before X-Plane says it again; the Live window's Flight
  card shows it, and how far X-Plane reads 4096 px there.
- **The camera's travels** (`orthostudio/live/cameralog.py`): every 2 s of flight, how far from
  the aircraft (along the ground) and how low above the ground the camera goes in external views
  (200 m or more from the aircraft), at cruise (the aircraft 3,000 m or more above the ground)
  and below; logged when Live stops, the last 20 runs kept in `_live/camera.json`. At FL350 and
  450 kt Live downloads about 14 GB an hour, three quarters for the camera's reach: the figures are
  what a reach for cruise would be set on. Far and low at once is what counts (far and high up,
  X-Plane reads only small levels), so each run also keeps the reach its camera needed
  (`cameralog.reach_needed`: how much farther from the aircraft than its own rings X-Plane read
  each level, on ZL16 terrain, in three dimensions). And a line says when an external view's
  camera reaches textures before Live built the level X-Plane reads there ("camera ahead of
  Live", `Streamer.outran`): the neutral colour on the screen, or a far level's copy.
- **The camera's reach**: every ring but the 256 px one and the 8192 px level is widened by
  `--camera-reach-km`, how far an external view may take the camera, in the cockpit too. 10 km
  since 2026-09-29, the user's choice after a test flight: nothing seen, a start's downloads a
  quarter smaller than with 20 km at LFLL in ZL17/19, a straight path's a tenth (the 8192 px
  level, which has no reach, is most of it). The test's cameras needed 15 and 20 km at times
  (`cameralog.reach_needed`), mostly far levels. 20 km before, 25 km until 2026-09-28 (25
  covered a camera the user took 22 km out on 2026-09-26; the user chose 20 for the cost of
  ZL17/19). X-Plane reads a level as soon as the camera comes within its ring
  and never reads it again, and the camera moves faster than any build: an external view comes
  without warning, and a quick zoom out takes the camera over textures the cockpit's rings leave
  at 2048 px. The reach applied only in external views for a few hours (2026-09-29, before any
  flight): the user asked about a Shift+4 and a fast zoom out, which would have found the neutral
  colour there. The one-ring-early reads of 8192 px files are built as such (`Streamer._level`:
  their 4096 px and 2048 px levels a ring earlier, where X-Plane reads them), which the reach alone
  covered only within its distance. The reach goes round the aircraft and its path, where a camera
  may be near the ground; the camera itself, where it is, counts in three
  dimensions from its own height, as X-Plane measures, with no reach round it
  (`Streamer.plan`): high up or far out it needs only the small levels. Along the ground and
  widened by the reach, a camera taken 57 km out at cruise had Live build 4096 px levels round
  the point below it, in bursts of 1.5 GB and 5 cores that stalled X-Plane on a 16 GB computer
  (2026-09-28).
- **The 8192 px level**: X-Plane counts a file's levels from its top, whatever its size (the
  fs_usage trace: an 8192 px file read whole at 11.9 to 12.2 km, its 4096 px level and below at
  20.7 km). So it reads each level of an 8192 px file where it reads the next smaller one of a
  4096 px file. The photos of the levels 4096 px and smaller are built at the 4096 px file's
  distances, nothing more downloaded. With the camera's reach they come before X-Plane reads
  them nearer than 43 km; the far levels, read at 81 km and at load before their photos (67.5
  and 110 km with the reach), get the copy of the level below: a file built from the 512 or 256
  px level has the level above written too, twice as wide, each pixel doubled straight from the
  BC1 blocks (`texture.needs_copy`, `double_bc1`, in `write_sparse`). A copy takes four times the
  disk of its level: with one above every level built, a five-minute flight near Lyon took
  27.7 GB, so there are no others. The 8192 px level itself gets its photos within ring 0 with
  the margin (15.1 km at ZL16, 3.8 km at ZL18) of the path ahead, the aircraft or the camera, in
  three dimensions from the aircraft's height above the ground (`sim/flightmodel/position/y_agl`)
  and the camera's (the aircraft's plus `view_y - local_y`), as X-Plane measures: at FL350 only
  within about 10.5 km of the track, none above 15 km. Its urgency is the distance left before
  ring 0 (13.6 km), where X-Plane reads it; at a start, X-Plane waits for them all. In flight, one
  still without its photos within 1.1 times ring 0 of the aircraft or the camera, its 4096 px
  level built, gets that level copied up into it first (`texture.copy_up`, in a worker, before
  the photos are encoded, never after; `Streamer.copied_up`, logged): late photos show the ortho
  as at ZL16, never the neutral colour. No camera reach: a camera taken farther, near the ground,
  may find the neutral colour. Each 8192 px build runs in a pool of its own (this computer's
  share, four at most: `machine.sharp_builds`) whose processes end with their texture
  (`max_tasks_per_child=1`): the twelve workers kept 14 GB
  after such builds, and the Mac, short of memory, froze X-Plane for up to 0.9 s (2026-09-28);
  the first smaller level is made by strips of rows (`imaging.mip_chain`), 1.3 GB at the peak of
  an 8192 px build instead of 2.3. The disk counts the far copies (`evict.texture_bytes`), and the
  disk budget makes an 8192 px texture coarser in its 8192 px file. X-Plane's texture quality
  below Maximum reads no 8192 px level: none gets its photos (as the quality is now, see
  "X-Plane's texture quality"). Estimated on the textures around
  LFLL at 250 kt: ZL16/18 about 1 GB at a start and 7 GB an hour, ZL17/19 1.4 GB and 13 GB an
  hour.
- **At cruise height** (`Streamer._cruise_height`): above 18,500 ft from the ground, the aircraft's
  height or a camera's lower down, no 8192 px level gets its photos (ZL17, ZL19 near airports), only
  the copy of its 4096 px one as X-Plane is about to read it, never the neutral colour; their photos
  again below 17,500 ft. Never at a start, where X-Plane gives no height. The screen cannot show
  that level from there: from 18,000 ft a pixel of a 4K screen covers 1.2 to 2 m of the ground right
  below (45 to 65 degrees of view), ZL17 is 0.84 m a pixel in France and ZL16 1.7, and the graphics
  card samples the levels the screen can show; looking ahead, the ground is two to five times
  farther. At 45 degrees of view a little of that level still blends in just above 18,000 ft: hence
  the copy. At FL300 those levels were about half of what Live downloaded and encoded in flight, and
  with them X-Plane's background workers were all busy 61 % of a flight on a hot Mac (the user's
  rule, 2026-10-01; computing each user's own screen and field of view is left for later).
  Descending, the photos come again 3 minutes before the aircraft is 13,000 ft from the ground (its
  vertical speed, `sim/flightmodel/position/vh_ind`), where they begin to show on that most
  demanding screen: about 20,500 ft at 2,500 ft a minute, 25,000 at 4,000, 17,500 when it descends
  slowly; the user's wish after a descent onto Oslo Gardermoen switched at 17,500 ft had 17 levels
  late, the last near 15,000 ft, none under 10,000 (2026-10-01, scenario `descent-engm`).
- **A start** is described in the next section. Without the plugin, or after a jump of more than
  20 km that nothing announced, X-Plane has loaded the area before its textures were built. Once
  every texture it may already show is built (urgency 0 or less), the log says so: what X-Plane
  read before stays the neutral colour until it loads that place again (a new flight there, or
  X-Plane restarted). No reload of the scenery is asked: `sim/operation/reload_scenery` keeps the
  textures X-Plane holds (27 September), and it did nothing at LGAV (the user, 29 September).
- The built levels are kept in `_live/ortho.json`; the imagery in `chunks.sqlite` in Live's folder,
  with a rollback journal. Not the photos of the 4096 and 8192 px levels (`UNCACHED_FROM`): the
  texture built holds them, and one built again after being made coarser downloads them again.

  **Disk writes** (the test pair of 2026-09-28, LFBO north-west, 15 minutes at 3,000 ft and
  230 kt, ZL17/19, both caches cold): Live wrote about 25 GB for its whole flight, AutoOrtho
  8 GB. AutoOrtho builds each texture in memory as X-Plane reads it and mostly keeps its JPEGs,
  written once. Live wrote:
  - about 8 GB of imagery: the WAL and the table wrote 2.5 times the pieces' bytes;
  - about 4 GB of levels written again under each sharper build;
  - about 13 GB of textures: 113 at 8192 px, 412 at 4096 px (AutoOrtho built 104 and 229, only
    what X-Plane asked).

  The journal, the photos left out and the levels kept bring it to about 14 GB. What remains is
  the price of real files: a texture ready for X-Plane weighs three to four times its JPEGs, and
  Live builds ahead of X-Plane's reads (the margin, the camera's reach).

- **Every 10 s**, the disk budgets (`orthostudio/live/evict.py`), each half of Live's one disk space
  (`disk_gb`, `--disk-gb`, 20 GB, 40 GB until 2026-10-12; never under `settings.disk_floor`, 10 GB
  at 16/18 and 20 GB with 8192 px textures, a smaller space raised to it: a 20-minute flight at
  17/19 given 4 GB took 11.7 GB of textures, those X-Plane may read never made coarser), the
  textures taking too the room the imagery leaves in its half
  (`Streamer.texture_budget`: over Norway, 2026-09-29, the textures filled their 20 GB while the
  imagery held 1.7 GB): over it, textures are made coarser, those behind the aircraft first (its
  track taken as ahead, `Streamer.behind`), then the least recently needed at their sharpness (seen
  only while X-Plane will read them as sharp as they are built: until 2026-09-30 every texture
  within 300 km counted as seen, so the oldest built went first, which on the way back were those
  ahead of the aircraft), only where nothing can show it. Sharp ones go back to 256 px from
  what is in the file where X-Plane holds none of their DSF tiles (`evict.xplane_tiles`: its window
  of 4 columns by 3 rows round the aircraft and the camera, with its lag) and reads only their
  256 px level from there (`Streamer.may_coarsen`: beyond the 512 px ring and the camera's reach).
  Small ones go back to the neutral texture only beyond 300 km, or leave no file at all off the
  squares whose files are made (`names.Names.holds`). Until 2026-09-29 both waited for 300
  km, about twice what X-Plane holds behind the aircraft: the measurement flight (LFBO to LFMT and
  back at FL300) saw it move its window 0.15 degree past a column's middle and let go of the tiles
  left behind at once, keeping 133 to 213 km of them at 43.6 N. The user chose to keep evicting
  only over the budget, not as soon as allowed. 50 textures at most a round, and never while
  X-Plane loads scenery (rounds of 500 every 30 s until 2026-09-28: one met a hitch of 387 ms); over the imagery's half (20 GB),
  the imagery downloaded longest ago is deleted. The probe saw X-Plane open textures again 1,276
  times for a sharper level and never for a coarser one: a texture made coarser behind the
  aircraft is never read coarser than X-Plane has it.

## A flight's start: X-Plane waits

The hold test (live/hold-test.md) showed the order in which X-Plane loads a
flight:
1. At the click on Start Flight, it saves the start in `Output/preferences/Freeflight.prf`
   (`_last_start`).
2. It loads the aircraft, telling plugins PLANE_LOADED, then FMOD_BANK_LOADED and LIVERY_LOADED.
3. Only then does it read the scenery.

A plugin that does not return from one of these messages holds the whole load.

- **The plugin** (`native/xplane_plugin/live_gate.c`, the same on every system):
  - on a Mac, `orthostudio/live/plugin.py` builds it with clang, one `.xpl` for Apple Silicon and
    Intel Macs in X-Plane's `mac_x64` folder. It is linked to a stub of X-Plane's XPLM framework:
    the name X-Plane's framework answers to, which Laminar's own plugins name too, and the
    functions it calls, from `XPLM_64.def`. So neither X-Plane nor its SDK is needed, and the file
    is the very one a link to X-Plane's framework itself gives, byte for byte but its signature
    (X-Plane 12.4.3, 2026-10-02). The Mac installer is built with it; a checkout builds it at
    Live's first start (Xcode's command-line tools);
  - the Windows and Linux plugins (`win_x64`, `lin_x64`) are built ahead, since those computers
    seldom have a compiler: `tools/live/plugin_builds.py`, on any computer with Docker, in an
    Ubuntu 20.04 for x86-64, with MinGW-w64 and gcc, into `native/xplane_plugin/prebuilt` with
    the digest of their sources, which a test checks. The Linux plugin asks for glibc 2.17 at
    most and opens libX11 only to show its window (`dlopen`): it loads without it. The Windows
    one needs only Windows' own libraries and X-Plane's `XPLM_64.dll`, whose functions it takes
    through an import library made from `XPLM_64.def`. Their installers ship these very files,
    which their build refuses once the sources have changed since (`plugin.ship`,
    `tools/package/build.py`);
  - as X-Plane starts (`XPluginStart`), it sends `LIVE1 HELLO`: refused (nothing listens on Live's
    port), it removes Live's links `z_osxp_live` and `y_osxp_live_overlays` from Custom Scenery,
    never a folder, before X-Plane lists its scenery (see "The pack"), unless that link's line is
    listed unchecked only (`listed_unchecked`, any path form): Live stopped as it should, and the
    pilot's place kept. Without Live it then stays idle until X-Plane quits: no socket (Live
    cannot join an X-Plane that listed its scenery without it), not a word in Log.txt, and on
    Windows X-Plane greyed as usual when it stops responding (`alone`);
  - at PLANE_LOADED it sends the streamer `LIVE1 LOAD lat lon`;
  - at the radio's FMOD_BANK_LOADED and at LIVERY_LOADED it sends `LIVE1 HOLD lat lon aircraft
    day=N utc=S` every 250 ms over UDP (127.0.0.1:49250), the flight's date and UTC time as X-Plane
    loads it (its season; whether it is night there), and returns only on `LIVE1 GO`.

  If nothing listens, it returns at once; if the streamer stops answering for 5 s, or goes away
  (its port closes), it returns too. There is no time limit otherwise: the five minutes it once
  allowed let X-Plane go on with 211 textures unbuilt at LFPG, ZL17 and ZL19 (27 September). The
  streamer answers go when no piece has arrived for a minute (`STALL_S`: no internet, or every
  request failing). The log gives the first request to fail each way, and that message the
  last one's reason, in curl's words (`Downloader.last_error`): on the user's Windows
  (2026-10-04) every request failed at once, curl unable to open its certificates' file, the
  test kit's folder named with an « ê », and the log said only « failed ».
  - The plugin lets X-Plane go when its first `HOLD` gets no answer within a second (no
    streamer). A new start planned under a start's load kept the streamer's loop 3 s, one pass
    over apt.dat for the airport among it, and X-Plane went on at LGAV and LCLK with nothing
    built (29 September). So the gate answers for a busy loop: past `STAND_IN_S` (0.5 s) without
    the loop reading the plugin's lines, it reads them itself, keeps them for the loop and
    answers a `HOLD` with the loop's last `WAIT` (`Gate.hold_at` gives it the start's name as
    soon as it is known), never for a loop gone more than `ALIVE_S` (60 s). The log says when
    it did ("Live was busy 2.1 s: the gate told X-Plane to wait meanwhile"). And the airports
    are read once, when Live starts, in a thread of their own (`route.AirportIndex`).
  - A new flight started from within a flight, with the same aircraft, loads no aircraft, so none
    of these messages come (LSGG, 26 September: X-Plane read the scenery without waiting, and the
    streamer reloaded it a minute later). The plugin held at message 115 (1) there until
    2026-11-01, which turned out to be `XPLM_MSG_WEATHER_DELIVERED` (SDK 4.4.0): 12.4.4 sends it as
    a flight's weather is delivered, before its scenery, but at every change of weather too (three
    changes of weather in flight, three of it, each held), and 12.4.3 not at all (alerts « could
    not locate image file for terrain » after a new location chosen in flight on the user's Mac).
    Probe plugins showed that X-Plane calls no plugin at all from the click on Fly to the end of
    the scenery load: no flight loop runs from the Flight Configuration screen's opening, its
    drawing calls plugins while it shows, nothing after the click. So Live pauses X-Plane itself
    there (`orthostudio.live.pause`, the user's choice B of 2026-11-01: the plugin keeps its holds
    at the loads it hears, the pause only for this case):
    - At that click X-Plane writes `Output/preferences/Freeflight.prf` in place, line by line,
      several times within a few milliseconds: three times with the flight running, then with the
      new `_last_start` near its top, before it reads any scenery. While X-Plane flies (its data
      fresh), `Pauser` is woken at every write (kqueue on a Mac, inotify on Linux; on Windows the
      file's size and time read every millisecond through a handle that leaves X-Plane every right
      on it), pauses X-Plane's process first (SIGSTOP; NtSuspendProcess on Windows), then reads
      `_last_start`: the one it had (the first writes, a change of weather, X-Plane quitting),
      X-Plane goes on at once (238 to 317 such pauses at a click on a Mac, the longest 1.03 ms; 19
      to 39 on Windows, the longest 0.19 ms); a new one, X-Plane stays paused. On the ground or
      while X-Plane loads, the writes only tell `Pauser` the start X-Plane has: a load is the
      plugin's.
    - The streamer's loop holds the paused start as it holds a `HOLD`: the click read in
      Freeflight.prf planned, X-Plane goes on (`Pauser.resume`) once everything it will read there
      is built, or nothing has downloaded for `STALL_S`; a start it cannot read within
      `PAUSE_UNREAD_S` (10 s) lets X-Plane go. Measured, the pause fell between « Starting new
      flight » and « Init dat_p0 », no scenery file open: on a Mac (12.4.4-r1) EGLL, LCLK, LGAV,
      LEMD, LFPG held 72, 12, 24, 4 and 22 s; on the user's Shadow (12.4.3-r2) LEMD and EGLL held
      20.05 and 20.04 s against 23 to 26 ms unpaused.
    - Nothing of X-Plane runs while it is paused, the plugin neither: Live shows the plugin's
      loading window itself (`orthostudio.live.pause_window`: its sizes, colours and words, the
      start's whole name from the first instant, « preparing the files » while the squares' files
      are made, before the textures are counted), a Cocoa panel of the app on a Mac (none when Live
      runs from a terminal), a topmost window of a thread of its own on Windows, on Linux a process
      of its own while the window is up (`orthostudio.live.pause_window_x11`, X11 through ctypes,
      the plugin's `live_panel_x11.c` window; apart from Live because Live's window is GTK in the
      same process and Xlib's error handler is one for a whole process). It reads a JSON line per
      change on its standard input, and at the end of that input (Live done, or gone however it
      went) the window goes. Without libX11 or a display it ends at once and is not started again:
      no window, and `LogLive.txt` says the wait.
      On a Mac the pointer over the paused X-Plane may turn into the spinning wheel. On Windows a
      click on a paused X-Plane greyed it as « Not Responding » and offered to close it: the plugin
      calls `DisableProcessWindowsGhosting` as X-Plane starts (only the process itself can), after
      which clicks during a pause showed neither (the user's Shadow). On Linux a paused X-Plane
      cannot answer the window manager's pings, and GNOME would offer after 5 s to force it to
      quit: for the window's time X-Plane's windows stop saying they answer them (`_NET_WM_PING`
      out of their `WM_PROTOCOLS`, as the plugin does at a start), and say it again after.
    - A guard process holds the reading end of a pipe whose writing end only Live holds: however
      Live ends, X-Plane goes on at that instant (a first test that looked every half second left
      X-Plane paused when the test was stopped mid-pause). Live's Stop lets it go too.
    - To watch: one X-Plane crash 30 s after a pause (an abort in the X-KeyPad plugin's thread,
      `malloc`), where the user's X-Plane had 14 such aborts since September with no pause. A
      pause stops every thread of X-Plane, other plugins' included, where the plugin's hold stops
      its main thread alone.
  - It notes every message X-Plane sends in Log.txt (`OrthoStudio XP Live: message 115 (1)`), in order
    among X-Plane's own lines.
  - In flight, every second (a flight loop), it sends `LIVE1 ASK` and reads the answer without
    waiting: `LIVE1 NOTE id lang=xx text` (the notice above) or `LIVE1 NONE lang=xx`. A notice
    shows in a small panel in the top right corner of X-Plane's window (the loading window's
    file, its cross taking a click without taking the keyboard), which closes it for that
    notice.
  - While it waits, X-Plane's window is not redrawn: at a first flight its loading screen still
    names the aircraft, and at a flight change the Flight Configuration screen stays. No plugin can
    wait inside "Reading scenery" itself, because X-Plane calls no plugin there. So the plugin
    shows its own loading window over X-Plane: where, how many textures are left, the download rate
    in MB/s, or « every photo in, building » once every photo they wait for is in (`LIVE1 WAIT n=
    b=`, `b` reaching `n`: a rate of 0.0 while the count went down read as stuck on the user's
    Shadow, 2026-10-11; two lines, the textures waiting for photos and those being built, were
    tried and dropped: the second stayed between 0 and 4 on a Mac), « N night textures to build »
    once only night textures are left at a start at night (`night=`, how many of those left are
    night ones, sent always, 0 too, which an older plugin reads past; the user, 2026-11-11), the
    seconds since the start was known, and a progress bar; the window's start card says the same.
    The bar is the share done of the count it shows, its textures and, while the start waits for
    them (`NightWork.start_night`), its night textures, each part's whole the most seen while it is
    wanted, going back only as work is added, never full with work left (`Arrival.progress`):
    counted in image pieces, the night textures after the photos, it stood at 38 % while the count
    fell from 3,560 to 24, then jumped to 100 % (Toulouse, 2026-11-11); the night of a start found
    to be day at the HOLD leaves the whole with its textures. At a first flight the streamer starts
    building before the plugin starts waiting. Each system draws it its own way, the same sizes,
    colours and words:
    - on a Mac (`native/xplane_plugin/live_panel.m`), the run loop does not turn, so it is drawn
      on the spot with `-display` and `[CATransaction flush]`;
    - on Windows (`live_panel_win.c`), the windows belong to a thread of the plugin's own, which
      draws them and takes the cross's clicks, whatever X-Plane's held thread does. And while
      X-Plane waits, its thread looks at its messages every 250 ms without taking any
      (`PeekMessage`, `PM_NOREMOVE`): Windows greys the window of a program that has not looked
      for 5 s, "Not Responding";
    - on Linux (`live_panel_x11.c`), with X11 (XWayland under Wayland), through a connection of
      the plugin's own, drawn at once and its events read every 250 ms while X-Plane waits, every
      second in flight; Xft draws the text where it is installed, else the X server's fixed
      font. Without libX11 or a display, no window: X-Plane waits all the same.
- **The gate** (`orthostudio/live/gate.py`) is on the streamer's side:
  - it watches `Freeflight.prf` and resolves the saved start: an airport through apt.dat, or a
    latitude and longitude. The plugin's window names it: the airport with its runway
    (`LFML runway 13L`) or its parking (`LSGG parking 44`, the airport's own name for it, with
    "parking" before a number or a code only), else the coordinates. A start X-Plane does not
    save there is known only from the plugin, by its coordinates;
  - it answers the plugin: `LIVE1 GO` once no urgent texture is left, `LIVE1 WAIT n b` otherwise
    (`b`: of the `n`, those with all their photos, being built: `Streamer.urgent_building`);
  - it answers GO at once to an `aircraft` hold while X-Plane's UDP position is live, because then
    X-Plane is flying and only a livery changed, except after a click on Start Flight less than 2
    minutes ago: X-Plane is leaving for that start, and its live position is still the old one
    (the `flight` hold at message 115 that was a second exception went with that hold, 2026-11-01);
  - a click counts as a start only once X-Plane goes through a load, that is when its position
    stops or the plugin speaks. X-Plane also writes `Freeflight.prf` when it quits, and a reload
    once came of it at LFPG.
- **The expected start** (`Arrival` in `orthostudio/live/streamer.py`) is planned as soon as it is known:
  - the click;
  - the plugin's position, if nothing else said where, or if it says elsewhere (below);
  - `--from`.

  The streamer keeps planning it, not X-Plane's last position, until X-Plane runs there. This holds
  even while X-Plane still flies where it was, just after the click. The plugin's position, far
  from the expected start, replaces it, unless it is where X-Plane flew last as that start was
  chosen, however long ago it last sent its position, and X-Plane has not started again since
  (`Arrival.holds_for`): after a teleport the plugin may still read the old position, for as long
  as X-Plane loads; an X-Plane started since reads none, and may load a situation saved there. A click is no proof against it: X-Plane writes its last start into
  `Freeflight.prf` as it quits, and a flight then loaded another way (its web API, a situation)
  saves none (X-Plane, flying at Nice, quit writing Geneva; a flight to Lyon was then answered for
  Geneva and went on with nothing built, 2026-11-11).
- **Read early**: when X-Plane runs at the start, the log says so once everything is built
  (no reload, see above) only if it may have read a texture before it was built:
  - not if the plugin held it until GO;
  - not if everything was built before X-Plane was told where to start;
  - with `--from` and no plugin, only if X-Plane started before the area was ready
    (its process start time against the moment the last urgent texture was built).

## X-Plane's texture quality

X-Plane's texture quality (Settings, Graphics: Texture Quality) sets the sharpest level it reads
of each texture: Maximum (5) reads them all, each notch below one level fewer (High, 4, stops at
2048 px of a 4096 px texture, measured; below, assumed: `rings.finest_for_quality`). Live builds no
level sharper than X-Plane reads (`Streamer.finest_quality`, `rings.finest_readable`): built as
at Maximum, it would write about 45 % more and download 60 % more below it (estimated). X-Plane applies
a new quality at once, at « Resume flight »: read once at Live's start from `X-Plane.prf`
(`_tex_res`), a quality raised later left X-Plane reading levels Live never built, the neutral
colour, green-grey, under the aircraft for the whole flight (a user's report, 2026-11-12).

**What X-Plane does** (measured on a Mac, X-Plane 12.4.3, 2026-11-13, files' access times):
- lowered, it reads again some 3,400 textures 3 s after « Resume flight », at the coarser levels,
  which are there;
- raised, it reads again only the textures round the aircraft whose sharper level it now reads,
  within about 24 km (about fifteen, and their night textures), 3 to 6 s after;
- the setting is the private dataref `sim/private/controls/reno/tex_res` (int, 5 Maximum), which
  follows the slider at once; X-Plane registers it after its plugins start
  (`XPLMFindDataRef` gives none in `XPluginStart`), so the plugin looks for it again once a
  second until it is there;
- held in a flight loop of the plugin's, 15 s at once after a rise (two tries), X-Plane read
  none of those textures meanwhile and read them all right after: its loading of textures waits
  for its main thread.

**The rule**: Live builds for the quality X-Plane has now, read in X-Plane by the plugin; a raise
in flight holds X-Plane, as a start does, until what it will read sharper is built, night textures
included, with no time limit (the user: « ne redémarre pas xplane tant que les textures nettes ne
sont pas finies, nuit incluse »).

- **The plugin** (`live_gate.c`) reads the quality every frame (`texture_quality`):
  - its first reading, and every change, go to the streamer: `LIVE1 QUALITY q`, before each
    start's `HOLD` too, so that a start is built for it (a quality changed in X-Plane's menu, a
    departure prepared at the old one); every second in flight the ask says it too
    (`LIVE1 ASK max=.. n=.. q=5`): a lower quality, or a streamer started in flight;
  - a rise in a flight (an aircraft loaded) holds X-Plane right there, in that frame, before
    X-Plane reads anything: `LIVE1 SHARPEN lat lon q` every 250 ms, answered `WAIT` or `GO` as a
    `HOLD` is, with the same loading window (its first line « Quality raised: sharper textures
    around the aircraft », « Qualité relevée : textures plus nettes autour de l'avion »), the same
    Windows ticks, the same ways out (no streamer: at once; silent 5 s; X-Plane quitting);
  - a lower quality, or a rise before any flight, holds nothing;
  - an X-Plane without the dataref: none of these lines, a line in `Log.txt` once in flight, and
    the streamer keeps X-Plane's preferences as read at its start (as before 2026-11-13).
- **The gate** keeps the quality the plugin last said (`Gate.quality`), and passes on `SHARPEN`
  with its position; its stand-in answers a late loop's `SHARPEN` with the loop's last word for it.
- **The streamer's loop** (`streamer.run`), before answering any line read with it: a quality the
  plugin said that changes the sharpest level (`Streamer.set_quality`, which recomputes the zooms
  whose 8192 px level X-Plane reads) is logged and planned at once, at the start awaited or where
  the aircraft is, so no answer counts at the old quality. Not with `--quality` set
  (`follow_quality`). Then a `SHARPEN`:
  - with a start awaited, or nothing flown yet: `GO` at once (the start's own hold counts at the
    new quality);
  - else X-Plane waits (`Sharpening`, `sharpen_count`) for the planner's urgent textures, those it
    may show now, at the new quality, and, at night, for the night textures of every one of them,
    made from the sharper level (`NightWork.left_of`), by the start's team of processes
    (`NightWork.rush`). Its count, bar and words are a start's (`_Bar`);
  - measured on a Mac the same day, the quality raised in X-Plane's menu at night, from medium
    (3): X-Plane held 6 s while Live built 45 textures and their night ones; of the textures Live
    wrote meanwhile, X-Plane read none during the hold and, from 1 s after it, every one within
    20 km of the aircraft, day and night (the access time macOS sets at a file's first read after
    its writing, `scratchpad` tool `firstreads.py`); farther ones it read later, as it came nearer;
  - `GO` once none is left, or when nothing has downloaded for a minute (`STALL_S`, as at a start):
    a piece missing 10 s is built from the zoom below and again later (`STUCK_S`);
  - the plugin asking no more for 5 s (`SHARPEN_GONE_S`): the wait is over.
- **Tests**: the plugin against the stand-in X-Plane, whose `set:` sets the dataref
  (`PluginTest`); the gate's lines (`GateTest`); a quality raised in flight and at a start, built
  for real (`RunTest`); the streamer's levels (`test_live_zoom`); the night's part
  (`test_live_night`).

## The season

The photos are mostly summer's. Live gives them a season's colours (the user, 2026-10-13: spring,
summer, autumn, winter, with sliders; no snow, agreed the same day), `orthostudio/live/look.py`.

- **Five values per season**, each a deviation, 0 changing nothing, applied in this order:
  brightness, contrast and saturation (OrthoStudio XP's arithmetic, `textures/colour.py`), then
  temperature (red up and blue down by 12 % at +1, the reverse at -1) and vegetation: what is green
  in the photo as delivered (green above red and blue, fully at 12 steps) has its hue turned
  towards yellow and russet by up to 75 degrees (+1), or its saturation raised by up to 60 % (-1);
  fields, roofs, roads and water stay.
- **Live's values** (`DEFAULT_LOOKS`), measured (the user: « au plus réaliste possible »,
  2026-10-13) on Sentinel-2's true-colour images (Microsoft's Planetary Computer, no account), the
  same 6 km squares in July and in January, April and October, cloud- and snow-free (Toulouse and
  Provence in all four, Lyon and the Jura in April and October, Lavaux in October), each season's
  image scaled to July's mean grey (the light is X-Plane's own, by the sun of the flight's date).
  Most of the change is field by field (a crop grown, harvested, ploughed): the five values that
  best turn July's pixels into the season's reproduced a third of July to January (the light,
  mostly; 1 % once it is out), less than 6 % of July to April and nothing of July to October. In
  the south of France July is the yellowest: winter wheat and meadows are greener in January and
  April. What is common, by the mean colour of what is green in July: olive in January ((46, 64,
  37) to (77, 72, 39)), a little yellower in April ((39, 61, 35) to (55, 62, 38)) and paler in
  October ((40, 61, 35) to (51, 64, 42)). Hence: summer as delivered (Bing's photos are mostly
  summer's); spring vegetation +15 %; autumn saturation -10 %, vegetation +25 %; winter saturation
  -15 %, vegetation +60 %. A first set chosen by eye (greener spring, russet autumn, colder winter)
  was dropped: the user found summer and spring alike, and the measures said neither was real.
- **Applied to the encoded blocks** (`look_blocks`, in `texture.encode_levels`, every level): a
  BC1 block's two colours go through a table of the 65,536 5:6:5 colours in the look (4 ms to make,
  kept per look in each encoder), its pixels' mixes kept, and the block is put back in four-colour
  mode if the change swapped its colours' order. Applied to the pixels before the encoding it cost
  as much as the encoding (0.30 s against 0.29 s for a 4096 px texture, 1.2 s against 1.14 s for
  an 8192 px one, M4 Pro, 2026-10-13); on the blocks, 20 ms for a 4096 px texture, the same to the
  eye: 3.0 steps of 255 from the exact colours on average, against 2.5 for the pixels changed then
  encoded (four places, three seasons). A table of the pixels at 7 bits a channel took 0.10 s.
- **Which season**: `season` in the settings, "auto" by default, or one of the four always.
  "auto" takes the date of the flight X-Plane starts: Freeflight.prf's `_time`, written with
  `_last_start` at the click on Start Flight (`{"local_time": {"day_of_year": 259, "latitude":
  37.43, ...}}`, `gate.read_flight_time`; X-Plane counts days from 0), at the start's latitude:
  the astronomical seasons (spring from 20 March, summer 21 June, autumn 23 September, winter 21
  December), the other way round south of the equator, summer between the tropics (23.44°). Read
  at every start the streamer expects (`streamer.run`'s `expect`: the click, the window's Prepare,
  the plugin), before it is planned; at Live's start, the date X-Plane saved last at the
  departure's latitude (else the latitude saved with it), today's when there is none. Never in
  flight (the user, 2026-10-13): X-Plane reads each texture once.
- **A new season** (`Streamer.choose_season`, or a pack recorded in another look at Live's start):
  every texture built is forgotten as built and kept as stale (`stale`, with its level), so that
  the planner builds it again as for a first flight; its file keeps the old season until then. A
  texture encoded in the old season finishing later is stale too (`look_gen`, carried by each
  job). The builder makes stale files neutral again a few at a time, those not queued or building
  (`drop_stale`, at most 0.2 s every second), as the disk budget does: their disk is no longer
  counted. Building again reads the far levels' photos from the imagery cache; the 4096 and 8192
  px ones are downloaded again, since they are not kept (`UNCACHED_FROM`): a start in a new season
  takes as long as a first start there. `_live/ortho.json` records `look` (the five values the
  textures recorded are built in), `season` and `stale`; a pack of before records no look: its
  photos as delivered. Delete (the textures) and a fresh start reset the stale ones too.
- **The window** (Settings, Season): the switch "Follow the date of the flight in X-Plane", the
  season chosen without it, and tabs with the five sliders of each season, saved with the form.
  A preview shows a place Live keeps photos of (`GET /api/live/look/sample?index=`, a square of 2
  x 2 pieces at ZL16, else the nearest zoom kept, read by the cache's own key: the newest pieces
  scanned a 2.3 GB cache for 12 s), as delivered and in the season, through `ui/look.js`, the
  same arithmetic per pixel, held equal to the engine's by `tests/test_live_look.py` (within one
  step); "Another place" goes through the places kept. Live's screen says the season the stream
  builds in (`snapshot()["season"]`), else what the next start takes.
- **Covered squares** keep their colours: the user's own tiles are built by OrthoStudio XP.

## The map

The window's map (`orthostudio/live/ui/livemap.js`) sits on a base map meant to be shared,
`orthostudio/live/ui/basemap.js`: Leaflet (OrthoStudio XP's vendored copy, a classic script),
OrthoStudio XP's aerial base map (`GET /api/map/{provider}/{z}/{x}/{y}`, its `map_router`
included in Live's app over Live's sources, with its cache), the country borders (its
`vendor/borders/borders.json`, decoded by its `geo.js`) and the airports of the view (`GET
/api/airports/in`, answered by Live's app from OrthoStudio XP's index, `~/.orthostudio/
airports.sqlite`, only if it is already there: building it takes minutes). They are drawn as the
Plan's map draws them. The Plan's own map (2,600 lines, most of them about choosing tiles and
drawing zones) is not reused: OrthoStudio XP's Plan map takes `basemap.js` once Live joins
OrthoStudio XP. Until then nothing of OrthoStudio XP changes.

Over it, Live's layers:
- the aircraft, an airliner seen from above, 48 px (`PLANE_PX`), pointing along its true heading as
  X-Plane's (`aircraft.hdg`, from `sim/flightmodel/position/psi`; the track, `aircraft.trk`, leaves
  it with wind, on the ground, and when a position set from outside gives the aircraft no speed:
  the user's note in the measurement flight, 2026-09-29), and the camera in external views, from
  the status the page reads every second (`camera`); the map follows the aircraft while "Follow the
  aircraft" is ticked (`#map-follow`): a drag in flight unticks it for that flight, a drag with no
  aircraft does not, and it ticks itself again once the flight is over (the user, 2026-10-09:
  always on by default). A flight starting brings the map to zoom 10 (about 60 km across) on the
  aircraft, unless it is closer already (`START_ZOOM`). The window's Prepare brings it to the
  departure the same way (`POST /api/live/prepare` answers where it is, `at`; `livemap.js
  showDeparture`), and unticks "Follow the aircraft" when an aircraft is followed, as a drag would
  (the user, 2026-10-31);
- the SimBrief route: the streamer's plan while the aircraft flies it (`flight_plan.following`,
  `flight_plan.points`, at most 400), else the one the button asked for; before
  a flight, the button asks simbrief.com for the pilot's last plan (`POST /api/live/map/route`),
  never on its own;
- the regions installed, their one-degree squares shaded (`GET /api/live/map/regions`, from
  `aoregions_map.json`);
- the textures built, by how sharp they are on the ground: the zoom of their photos, not their
  pixels (`GET /api/live/map/built`, `orthostudio/live/mapdata.py`). A tile of the mesh has a
  zoom of its own that says only how big it is (a ZL16 tile is 6.8 km wide, an airport's ZL18
  tile 1.7 km), and the file that fills it takes as many pixels as its photos need: a ZL16 tile
  with ZL17 photos is 8192 px, an airport's ZL18 tile with ZL18 photos 4096 px, and the second
  is the sharper. Coloured by pixels, the map showed it the other way round, and "a ZL16 at
  8192 px" meant nothing to a pilot (a user, 27 September). Colours: ZL19 (about 20 cm a pixel)
  violet, ZL18 (40 cm) blue, ZL17 (85 cm) green, ZL16 (1.7 m) amber, ZL15 (3.4 m) orange,
  blurrier grey. Each texture's rectangle, where it is, the sharpest first, at any zoom; beyond
  15,000 in the view, the blurriest are grouped by one-degree square, fainter, under the others.
  A first version grouped them all by square with the sharpest's colour, which hid the sharp
  textures near the aircraft; a second grouped the blurry ones by square in any view wider than
  3 degrees, which greyed whole squares of sea where only an island or a coast had textures,
  not where they were zoomed in (Malta, 2026-10-19).
  Each layer is drawn opaque in a pane of its own made see-through as a whole: an airport's
  textures over the ZL16 ones do not add their colours up, which first made them look darker
  than they are. The regions' shade is
  shown only up to zoom 6. The texture under the pointer is told in a bar over the map's top, one
  line or two, which the header's "Details" box hides (remembered in the window's storage), and
  told again when it changes while the pointer rests on it; a tooltip, and then a line below
  the map, were too present (the user): `GET /api/live/map/texture?lat&lon`,
  the smallest texture there, said sharpness first ("Photos of ZL17: 83 cm a pixel · a tile of
  the mesh 6.8 km wide (ZL16) · 8192 px, 45 MB · last within reach 4 min ago", and whether it
  waits to be built again); asked once the pointer rests on another texture; a grouped square
  says how many and the sharpest photos. While
  Live streams they come from the streamer's memory (`Streamer.built_now`, handed to the window's
  control by `session.follow(attach=...)`), versioned: the map asks every 3 s with the version it
  has and gets `same`, nothing copied, while nothing was built or made coarser; the rectangles of
  a version are computed once, each texture name's once and for all. Measured with 50,000
  textures built: 0.5 ms copied under the streamer's lock, 12 ms of rectangles (97 ms the very
  first time), a zoomed view of 1,452 textures filtered in 2 ms (62 KB), a wide one of 322
  squares in 9 ms (6 KB). Otherwise, the pack's `_live/ortho.json`, which the streamer writes
  every 30 s. Asked again at once when the view moves.

## Not yet

- A jump of X-Plane's clock into the night in flight: X-Plane reads the night textures once it is
  night and lights them a square after another, a minute or more over Los Angeles, and never
  faster when Live had them ready: made in 10 s by a team of processes (tried, not kept), the
  screen lit at the same pace on a Mac, and on a Windows computer X-Plane's workers, all busy from
  the jump on, kept Live to one process anyway (2026-11-11).

- Turns tighter than the two-minute look-ahead are caught by the rings' margin and by X-Plane's
  next ring, not predicted (none late on the turning test flight at ZL17/19, 2026-10-12, where
  ninety seconds left 4).
- Linux flown with X-Plane: everything is built for it and tested with a stand-in X-Plane, Live's
  whole test suite passes on Linux, but no flight yet (Windows is flown, on the Shadow's
  campaigns). Nor is it known whether
  X-Plane names its background workers on Windows as on a Mac and Linux (see "X-Plane's
  background workers" in "The loop": Live goes without them where it does not).
- A jump X-Plane makes without the plugin's hold (the map, no new flight) may find no files in its
  new squares for a few seconds, and a Live that stops while X-Plane flies on leaves the squares
  ahead without files: X-Plane never reads a DSF again once it has loaded it. The plugin's HOLD
  says where X-Plane loads a flight, and those squares are made too (`Streamer.hold_at`).
- OrthoStudio XP's Plan map on the shared base map (`basemap.js`), once Live joins OrthoStudio XP.
- A start in the air is planned as on the ground: its 8192 px levels round the start all waited
  for, where X-Plane at FL300 reads about a third of them (the review of 2026-09-30). X-Plane has
  no altitude for the aircraft while it loads a flight: the plugin read an altitude of 0 and a
  height above the ground of -2,090,546 m at its HOLD, for a start at FL300 (2026-09-30).
- Night lights: OrthoStudio XP's own tiles have no night texture yet (its option to come, with
  the same code); X-Plane 12's own `LUMINANCE` and `LIT_FADE_PARAMS` on the night lines not tried,
  which may carry towns farther.
