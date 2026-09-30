# Replayable test flights

Test flights that X-Plane flies by itself, the same way every time, each leaving its files and a
report. They make the measurements Live's design rests on repeatable: X-Plane's window of DSF tiles
at another latitude, AutoOrtho and Live under the same flight, a pack on a hard drive. One command
flies a series of them, and nobody has to take off, climb or watch.

    uv run python tools/live/flights/fly.py scandinavia-columns scandinavia-rows
    uv run python tools/live/flights/fly.py --streamer live --cold <scenario>
    uv run python tools/live/flights/fly.py --streamer autoortho --ao-settings default --cold <scenario>
    uv run python tools/live/flights/fly.py --streamer autoortho --zooms 17/19 --cold <scenario>

## As in X-Plane

X-Plane itself runs and draws each frame. It loads its own scenery and reads its own textures, with
the user's scenery, settings and aircraft, as in a flight. Only the aircraft's motion comes from the
scenario, set by the test pilot plugin at each frame:

- **in X-Plane's own frame**: the position is converted by X-Plane (`XPLMWorldToLocal`) in its own
  thread, so X-Plane moving its local origin as it loads scenery moves nothing. The drones over UDP
  of 2026-09-29 wrote local coordinates in an old frame and threw the aircraft a degree away, which
  shifted X-Plane's DSF window and spoilt the measurement;
- **on X-Plane's clock** (`total_running_time_sec`): a pause, or Live's plugin holding X-Plane,
  stops the path too. Placing the aircraft by latitude and longitude over UDP (VEHX) stopped
  X-Plane's clock and its time of day;
- **with its velocity**: set a second ahead with the position, so X-Plane, and Live's look-ahead,
  see the aircraft move;
- **with the same sky and light**: static clear weather, no wind, and the scenario's time and day.

The aircraft does not fly its flight model: the path is on rails, level, turns flat. That is what a
measurement of the scenery wants. A comparison of AutoOrtho and Live under an airliner's own flight
(X-Plane's autopilot flying) is still to design.

## The pieces

- `native/xplane_test_pilot/test_pilot.c`: the plugin. It does nothing without a scenario. Every
  second of the wall's clock it looks in `Output/osxp_test_pilot/` of X-Plane's folder for
  `scenario.txt` (a new one: flown, then renamed `scenario.done`) and `quit` (X-Plane quits). It
  writes what it does to `events.txt` and every frame of the path to `frames.csv` (time, frame
  duration, position, camera, X-Plane loading scenery). It marks each of X-Plane's scenery loads in
  `Log.txt` ("scenery load at LAT LON, path T s", and "scenery loaded in S s" at its end): its lines
  fall in order among X-Plane's, so the DSFs X-Plane logs after a mark came with that load. X-Plane
  keeps its own lines until a plugin writes one (a row's DSFs were still not in the file 5 min after
  their load): the end's mark puts them there.
- `tools/live/flights/scenarios/*.txt`: the scenarios, in the plugin's statements (listed in
  `test_pilot.c`'s header): a start, legs of a duration, a turn rate and a climb rate at one ground
  speed, the weather, the time, cameras, screenshots.
- `tools/live/flights/fly.py`: the runner (below).
- `tools/live/flights/report.py`: the report of one flight, from its folder.

A flight's steps in the plugin: waiting (10 frames, then the weather and the time set, and the
aircraft placed at the start with `XPLMPlaceUserAtLocation` unless X-Plane is there already), placed
(X-Plane pauses after a start in the air: the plugin takes the pause off; 3 s of X-Plane's clock
there, then the path), flying, done (X-Plane paused, the override off).

## The runner

X-Plane starts once for a series, straight into the first scenario's start in the air. Its hidden
command-line options, measured on X-Plane 12.4 (2026-09-29):

| option | what it does |
|---|---|
| `--pref:_show_qfl_on_start=0` | no main menu: straight into the flight. Plugins' flight loops do not run at the main menu |
| `--load_llah=LAT,LON,FEET,HEADING` | the flight starts there, in the air: the altitude in feet |
| `--load_acf=PATH` | the aircraft, from X-Plane's folder |
| `--start_running` | the flight running once loaded |
| `--no_save_prefs` | the preferences never written: the user's settings and `Freeflight.prf` stay as they were |
| `--no_modal_alerts` | fewer dialogs; it does not skip the plugin report X-Plane shows after a crash, which needs a click |

From the launch to the path's start: 40 s. Each scenario after the first starts where it says: the
plugin places the aircraft there, or finds it there already when the scenario starts where the one
before ended (no second start then: 2 s from one scenario to the next over Norway). At the end the
runner writes `quit` and does not wait for X-Plane. X-Plane asks "Quit the simulator?" at a first
`sim/operation/quit`, and waits for a click (11 minutes, 2026-09-29; the "25 s quit" of the first
test was the user's click), plugins' flight loops stopped meanwhile; a second one over UDP is its
answer: X-Plane shuts down at once, cleanly. So a small process the runner leaves behind sends it
every 2 s until X-Plane is gone, then stops the streamer. `--keep-open` leaves
X-Plane up for the next series, which then starts at once. Never used: `XPLMReloadPlugins` (it
crashed X-Plane through FlyWithLua), X-Plane's reload of the scenery, a kill of X-Plane. UDP's
`sim/operation/quit`, AppleScript's quit and a SIGTERM do nothing at the main menu, where plugins'
flight loops do not run.

Streamers, each started before X-Plane as the user starts it (X-Plane reads the list of scenery
packs at start-up), and stopped only once X-Plane has quit: the runner's small process above then
gives its Ctrl-C to the streamer and to every process under it, each process group once, and a
SIGTERM to any still there 2 min later. AutoOrtho's mount worker has a group of its own: a Ctrl-C to
AutoOrtho's group alone left it running, its region mounted (2026-09-29).

- `none`: X-Plane alone (the default).
- `live`: Live's `stream` from this checkout, with the user's Live settings and folder. `--cold`
  gives it a home of its own under the series' results: nothing built, nothing downloaded, the
  user's settings copied without their folder and SimBrief name. `--hold-whole-load`, a test of
  2026-09-30: Live idle for the whole of each X-Plane load in flight (nothing downloaded, encoded or
  copied up, only the squares' files X-Plane needs made), where it otherwise holds back its
  encoding for the first 30 s: whether Live's work delays the tiles X-Plane loads.
- `autoortho`: AutoOrtho without its window (`-H`), from the sources the user's AutoOrtho Dev runs
  (`~/Desktop/Dev/autoortho4xplane`, its Python in `~/.autoortho-dev-venv`). AutoOrtho writes its
  settings file at every start, so it always runs from a home of its own under the results (`HOME`
  set to it), never from the user's: `--ao-settings mine` (the default) copies the user's
  `~/.autoortho`, `--ao-settings default` leaves AutoOrtho its own defaults and gives it only where
  X-Plane is. Its cache is the user's, or with `--cold` one of its own. Its log is always its home's.
- `--zooms 17/19`: the streamer's max_zoom and max_zoom near airports for the series (AutoOrtho's
  settings file, Live's `--max-zoom` and `--max-zoom-airports`), whatever the settings say.

A cold run's heavy files are deleted once its streamer has stopped: Live's folder in its home (its
pack, the textures it built, its downloaded imagery) and AutoOrtho's cache and DSFs. A cold
AutoOrtho run of 2 minutes left 3.1 GB, with 77 GB free on the disk (2026-09-29). What stays is
small: the settings, the logs, and Live's record of what it built (`live-built.json` in the
series' folder), which its map reads. The user's own settings files, Live's and AutoOrtho's, are
only ever read. The runner refuses to
start when X-Plane runs without the test pilot, when a streamer is asked for but X-Plane already
runs without it (its scenery would not be read), and when Live or AutoOrtho runs already (their
windows'). With `--keep-open`, a next series goes on with the X-Plane and the streamer left
running.

## The window

`tools/live/flights/bench.py` opens a window ("Vols de test", in French: the developer's own tool),
made as Live's window is (OrthoStudio XP's window, its API's guard and its style sheet), on a port
of its own (8661), apart from Live's window, which is for its users and never speaks of AutoOrtho:

- what to fly: scenarios, ticked (each with its title, the `title` line of its file, and its
  length), or a campaign; with X-Plane alone, Live or AutoOrtho, AutoOrtho's settings (the user's or
  its defaults), the zooms, cold, and X-Plane left open;
- the flight under way: the campaign's run, the scenario, a bar of the path's time, the runner's
  output, and a Stop button (the runner's Ctrl-C: it writes what it has and quits X-Plane);
- the results, newest first: a click shows a series' summary, each flight's report and its
  screenshots, or a campaign's table (its row says the setups it compares, and whether the campaign
  went to its end); two series or more ticked are compared: their figures in one table a scenario,
  then their screenshots of each same moment side by side, each with its sharpness against the
  first's (below);
- the camera: the scenario's, or the user's own. A scenario's cockpit (`camera T0 T1 cockpit
  [PITCH]`) is held every frame: X-Plane's eye point, straight ahead, 10 degrees down unless the
  scenario says otherwise. Until 2026-09-30 X-Plane's own view stayed, and it was not the same from
  one run to the next: one Norway run looked at the panel, three at the horizon, and X-Plane's
  frames and memory differed with it (the review of the flights of 2026-09-29), so runs before that
  do not compare on those figures. "Enregistrer ma caméra" flies one scenario with
  X-Plane alone and leaves the view to the user; the test pilot records it 20 times a second of
  path (`camera-record`): outside, the camera's place ahead of, right of and above the aircraft,
  its heading from the aircraft's, its pitch and roll; inside, the pilot's head; and X-Plane's field
  of view. It is kept as the scenario's camera (`build/flights/cameras/<scenario>.csv`, the one
  before set aside, dated), and the runs that ask for it (`--camera mine`) replay it where the
  aircraft is then (`camera-track`): outside through the plugin's camera, inside in the 3D cockpit
  with the pilot's head set; played again from its start when shorter than the flight (the user
  recorded 2 min 37 of a 20 min flight and asked for a loop, 2026-09-29);
- Live's own map (`livemap.js`, as Live's window draws it, its tooltips in French) while Live
  flies: the aircraft at the test pilot's last frame, its heading from the frames a second before,
  the scenario's path, and the textures Live built, by the zoom of their photos; and in a Live
  flight's result, what it built and the path flown. `benchmap.py` feeds it from the test's Live
  rather than from a streamer of the window's own: Live's record of what it built (the pack's
  `_live/ortho.json`, saved every 30 s: the cold home's pack, the user's own for a run with the
  user's folder, else the copy kept in the series' folder, `live-built.json`).

It runs `fly.py` with the options chosen, in a process of its own: a flight goes on when the window
is closed, and the window finds it again (`build/flights/bench-current.json`). `bench_app.py` makes
`dist/Vols de test Live.app` for the Dock, OrthoStudio XP's icon with an orange badge.

## Campaigns

A campaign flies the same scenarios under several setups, one after the other, and puts them side
by side: `fly.py --campaign ao-vs-live` reads `tools/live/flights/campaigns/ao-vs-live.txt`, whose
lines are `title WORDS` (for the window), `scenarios NAME...`, `run [ID] OPTIONS...` (the runner's
own options) and `compare ID ID...` (runs read side by side). `--pick 1,3` flies only those
comparisons (without comparisons, those runs), on the campaign's flights;
`--pick 1:toulouse-low,3:scandinavia-columns+scandinavia-rows` gives each its own. A run two of
them share is flown once, its X-Plane flying the flights of both in turn; each comparison's table
keeps to its own flights. The window ticks the comparisons and, under each, its flights. Each run
starts its streamer and X-Plane again (X-Plane reads the scenery packs at its start), and waits for
the X-Plane and the streamer of the run before to have quit; a run that cannot start is said and
the next ones fly. After each run, `<date time> <campaign>.md` in the results gets the table of the
runs so far, a column a run, then each comparison's: the start's time, downloads and writes, the
path's frames a second, frame times and frames over 50 and 100 ms, its downloads and writes, the
streamer's and X-Plane's cores and memory, and X-Plane's scenery loads.

`ao-vs-live`, over `toulouse-low` (20 min at 3,000 to 10,000 ft over Toulouse, turns, a climb and
a descent, a close and two far external views), all cold, the user's four comparisons
(2026-09-29), each at the same zooms on both sides: AutoOrtho with its defaults at 16/18 and at
17/19, then with the user's settings at 16/18 and at 17/19, each against Live at those zooms.
Six flights (each Live flight serves two comparisons), about 2 h 50, nobody at the controls.

## The photos' sharpness

Two flights with the same camera (the scenario's, or the user's replayed) take their screenshots
with the same framing at the same moments: where two of them differ is the ground, whose textures
differ; the sky, the aircraft and the panel are alike (`report.sharpness`). The photos are compared
by blocks of 32 px: those that differ by more than 4 of 255 on average in some photo are the
ground's (X-Plane's frame counter, top left, left out), and on them each photo's mean gradient
says the detail it shows. The comparisons give each photo's against the first's, and a campaign's
table the mean over its screenshots. AutoOrtho with its defaults and Live, both at 16/18, cold,
the user's camera: +0.3 % for Live over 5 photos (2026-09-29), the same.

## Results

`build/flights/<date time>/<n>-<scenario>/`, one folder a flight: the scenario as sent, the
scenario's part of X-Plane's `Log.txt`, `frames.csv`, `events.txt`, the screenshots it took (moved
from X-Plane's `Output/screenshots`), the streamer's output, `system.csv`, `run.json` and
`report.md`. The series' `summary.md` has the few lines that matter of each. `system.csv`
(`tools/live/flights/sampler.py`) is the computer every 5 s: X-Plane's and the streamer's memory
and processor time (the streamer with every process it started, those ended included: `ps -S`),
the bytes received from the network, and the bytes written to each disk (`ioreg`'s counters, as in
the AutoOrtho and Live comparisons of 2026-09-28). Since 2026-09-30, to see why X-Plane loaded a
tile of Norway 7.5 minutes after the two others of its column while its log says nothing: X-Plane's
whole memory (`phys_footprint`, its GPU's allocations included), the bytes it read from the disks
and its page-ins (`proc_pid_rusage`); the Mac's free memory, memory pressure, memory compressed and
swapped (`sysctl`) and heat (`com.apple.system.thermalpressurelevel`, "heavy" through every flight
of 2026-09-29 and 30, X-Plane alone's too); the bytes read from each disk; and `threads.csv`,
each of X-Plane's threads at each sample: its name, processor time, state and priority
(`proc_pidinfo`). All read from outside, as Activity Monitor does: `sample` cannot attach to
X-Plane, signed with the hardened runtime. The report gives:

- the frames of the path: their count, the mean, median, 99th percentile and worst frame time, how
  many took over 50 and 100 ms, and the worst five, with whether X-Plane was loading scenery. The
  scenario's screenshots are left out (a second from each): each takes a frame of about 100 ms;
- X-Plane's scenery loads: the window loaded at the start (its columns and rows), then each load
  during the path, where the aircraft was, the tiles it brought, those X-Plane had loaded before in
  the flight (so had let go of), and whether they lie in Live's model of X-Plane's window
  (`orthostudio.live.evict.xplane_tiles`). A tile outside it is one whose texture Live could make
  coarser while X-Plane holds it. When each tile came, from the load's first (X-Plane's clock, its
  last DSF logged), and every gap of more than a minute where none came;
- what X-Plane and the Mac did during each load, against the rest of the path: X-Plane's cores,
  its busiest threads and those slowed the most, what it read from the disks a second, its
  page-ins, the Mac's least free memory, worst pressure, memory compressed and swapped, worst heat;
- what the start (from the scenario sent to the path's start) and the path cost the computer:
  downloads, writes to X-Plane's disk and the streamer's, processor cores and memory (median and
  most) of X-Plane and of the streamer; X-Plane's whole memory and its reads, and the Mac's
  memory and heat;
- the test pilot's events, the streamer's errors, the screenshots.

## Limits

- macOS only for now: the plugin is built there (clang against X-Plane's XPLM framework); the
  runner's launch on Windows and Linux is written but not tried.
- The aircraft is the one X-Plane started with, for the whole series.
- A screenshot costs a frame of about 100 ms: the report leaves the second after each out.
- The network and the disks are counted for the whole computer: other programs' traffic and
  writes during a flight count too.
