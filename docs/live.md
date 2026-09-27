# How OrthoStudio XP Live works

A guide for users: what OrthoStudio XP Live does while you fly, what you do, and why it asks for so
few settings. For the design and the measurements behind it, see [specs/live.md](specs/live.md) and
[specs/live/](specs/live/).

Until its launch, OrthoStudio XP Live is switched off and changes nothing in the rest of OrthoStudio
XP. It is switched on by its own file, `~/.orthostudio/live.toml`:

```toml
enabled = true
```

It runs on macOS only for now (its pack relies on APFS clones), from OrthoStudio XP's folder with
`uv run python -m orthostudio.live <command>`. `ui` opens its window; the other commands do the
same from the terminal.

## What it is

Aerial imagery under your aircraft, anywhere in the region you fly, downloaded while you fly, as
AutoOrtho does: no tiles to build beforehand. The difference is where it sits. AutoOrtho stands
between X-Plane and its textures: X-Plane asks for a texture and waits while AutoOrtho downloads it.
OrthoStudio XP Live stands beside X-Plane: it downloads the textures before X-Plane asks for them and puts
them on the disk as ordinary files. X-Plane never waits for it, so there is nothing to freeze on.

## What you do

Open the OrthoStudio XP Live window: `uv run python -m orthostudio.live ui`. It shows what Live
needs (X-Plane, AutoOrtho's mesh, AutoOrtho not running, the plugin), what it does, and its
settings: the imagery source, the disk space for the textures and for the downloaded imagery (with
what each takes now, and a button to delete either), and Live's folder. Its log is at the bottom.

There is no setting for the texture quality or for how far outside views reach: Live reads the
texture quality X-Plane is set to, and builds sharp 25 km beyond the aircraft for outside views.
Less, and X-Plane would show the neutral colour, green-grey, up close or around a distant camera.

The zoom is set as in AutoOrtho: a maximum zoom (14 to 17, 16 by default) and a zoom near airports
(16 to 19, 18 by default, for the package's ZL18 textures, which are its airports' surroundings).
X-Plane picks by itself, by distance, which version of a texture it shows; the zoom sets the
sharpest version there is, the one seen up close. Below the package's zoom a texture is smaller
(1024 px at ZL14): less sharp near the ground, far less to download, shorter waits at a start. One
zoom above, a texture starts at 4096 px and grows to 8192 px where X-Plane will show it that sharp:
sharper near the ground, four times more to download for those textures. A higher zoom is sharper
near the ground only (within about 6 km at ZL17, 1.5 km at ZL19); a lower one is blurrier within
38 km, the ground under the aircraft at cruise too. A new zoom applies at the next
start of Live, X-Plane closed: Live puts every texture at its new size, about a minute, keeping
what it built and downloading nothing.

When a flight starts, with ZL17 or ZL19, a setting says at which size X-Plane waits for the
textures of the start on its loading screen. X-Plane reads a texture's size each time it wants it
sharper, but never reads again what it already has: what it loads at 4096 px while the flight
loads, within about 10 km of the start (where it reads a texture's 4096 px), stays 4096 px during
the flight, unless the scenery is reloaded. So the choice is between waiting longer and a sharper
start:

- **Full resolution**: X-Plane waits until the start is in 8192 px, as far as an external view
  goes (25 km). Sharp from the first second, and the longest wait: about two and a half times
  Normal's (at Lyon, 141,000 pieces of imagery against 54,000).
- **Normal, and 8192 px around the aircraft** (recommended): the start in 4096 px, and 8192 px for
  the few textures right around the aircraft (8 at Lyon): about 15 % longer than Normal. The rest
  of the start, within about 10 km, stays 4096 px during this flight.
- **Normal resolution**: the start in 4096 px only, the shortest wait. The start, within about
  10 km, stays 4096 px during this flight.
- **Normal, then reload in 8192 px**, as XPME's "load in normal resolution, then high": as Normal;
  then Live builds the start in 8192 px, as Full resolution does, and once it is built, a panel
  over X-Plane (and a button in the Live window) offers to reload the scenery. X-Plane shows
  "reload objects & textures" for a few seconds, then the start in 8192 px. The offer lasts while
  the aircraft stays within 10 km of its start.

Beyond the start, all four are the same: the textures ahead of the aircraft become 8192 px before
you come near them, with nothing to reload.

The imagery sources are OrthoStudio XP's: Bing Maps, Esri, EOX, the countries' own (Spain's PNOA,
the Netherlands' PDOK...), and the sources you added in OrthoStudio XP's Plan. A country's source
has nothing past its border: there, and wherever it has no imagery, Live takes Bing Maps. Textures
already built keep their imagery; delete them to build them again from another source.

The first start prepares the whole region of your AutoOrtho scenery (Europe, for example), once,
in about 2 minutes, so that X-Plane always finds every file it looks for. It takes almost no disk:
the files share their content with AutoOrtho's until they are filled in.

Then, for every flight:

1. Click Start in the window (or `uv run python -m orthostudio.live stream` in a terminal). It
   starts building around the airport of your last flight at once, while you start X-Plane.
   "From scratch" forgets every texture built and all imagery downloaded, as the very first time.
2. Start X-Plane (the window's Start X-Plane button, once Live is ready), choose your flight and
   click Start Flight. X-Plane then waits on its loading
   screen until the ortho around your departure is built, as it does with AutoOrtho. That takes a
   few seconds where you have flown before, and a minute or two the first time in a new area. The
   Live window counts down the textures still to build, and so does a small panel over X-Plane,
   with the seconds gone by and the download rate in MB/s. X-Plane's own window looks frozen
   meanwhile: it is waiting, it has not crashed.
3. Fly, anywhere. Another flight started from X-Plane's menu waits the same way. With "Normal,
   then reload in 8192 px", a panel appears at the top of X-Plane once the 8192 px textures around
   you are built: Reload the scenery, or Not now.
4. Quit X-Plane, then click Stop (Ctrl-C in a terminal). Stopped or closed while X-Plane still
   runs, Live waits for X-Plane to quit before it takes its pack out: X-Plane reads its textures
   from it. "Leave it installed" stops waiting; the window then offers to take the pack out once
   X-Plane has quit.

To make X-Plane wait, OrthoStudio XP Live puts a small plugin in X-Plane while it runs
(`Resources/plugins/OrthoStudio XP Live`), and takes it out when it stops.
- X-Plane loads a flight's aircraft before its scenery: the plugin holds it there until the streamer
  says the textures are built.
- OrthoStudio XP Live learns where you start the moment you click Start Flight, because X-Plane saves it
  in its preferences, so the building starts before X-Plane even loads the aircraft.
- The plugin never holds X-Plane in flight, and does nothing if OrthoStudio XP Live is not
  running. At a start it holds X-Plane as long as the ortho keeps arriving, with no time limit:
  sharp zooms can take more than five minutes in a new area. It lets X-Plane go when nothing has
  downloaded for a minute (no internet), or when you click Stop in the Live window; X-Plane then
  shows the neutral colour where nothing was built, and gets its scenery reloaded once it is.
- In flight, the plugin asks the streamer every second whether a reload of the scenery is offered
  (the "then reload" start), and shows its panel when it is.
- Live asks the imagery source for as many pieces at once as its servers take, as OrthoStudio XP
  measured them: 128 for Bing Maps.

Without the plugin (`--no-plugin`), X-Plane does not wait. Where a texture is not built yet, it shows
a plain placeholder colour (never black). Once everything it shows is built, OrthoStudio XP Live asks it
to reload the scenery, once.

## What happens while you fly

Every two seconds, OrthoStudio XP Live reads where the aircraft is, where it is heading and how fast (as
AutoOrtho does, over X-Plane's network data), and where the camera is. It looks five minutes ahead
along the track, and for every texture within 300 km it works out how sharp X-Plane will want it
when the aircraft, or the camera, passes closest. External views count: X-Plane measures from the
camera, so a chase or free camera far from the aircraft gets its textures sharp too. What is
missing is downloaded and built, the most urgent first, and replaces the file on the disk. X-Plane
picks the new file up by itself the next time it wants that texture sharper.

External views need more than that. With Shift+4 the camera can go kilometres away in a second,
much faster than anything downloads. X-Plane reads whatever is in the files there at once, and it
does not read a level again once it has it. So OrthoStudio XP Live keeps the sharp levels built 25 km
farther around the aircraft than the aircraft alone needs (`--camera-reach-km`), and an external
view shows the right imagery as soon as you switch to it, as with AutoOrtho. A camera taken farther
than that may find a placeholder, which X-Plane keeps until it loads that area again.

X-Plane decides how sharp a texture must be from its distance, in rings. For a normal texture
(about 7 km across):

| distance to the texture | what X-Plane reads | imagery |
|---|---|---|
| 130-230 km, when X-Plane loads the area | 256 px | ZL12 |
| 72 km | 512 px | ZL13 |
| 38 km | 1024 px | ZL14 |
| 21 km | 2048 px | ZL15 |
| under 12 km, with texture quality at Maximum | 4096 px | ZL16 |

Textures around airports (ZL18, about 1.8 km across) follow the same law four times closer, up to
ZL18 within about 3 km. OrthoStudio XP Live downloads each texture at exactly the sharpness X-Plane will
read, and only there: the 4096 px level is fetched only for textures you pass close to, and never
if X-Plane's texture quality is below Maximum, because X-Plane then never reads it.

## Why there are no zoom, prefetch or waiting settings

AutoOrtho's settings are there to manage X-Plane's wait. Since X-Plane is blocked while AutoOrtho
downloads, you choose how much to download (maximum zoom, zoom near airports, zoom by flight
level), how long X-Plane may wait before it gets a blurry texture instead (maximum wait, time
budget, fallbacks), and how far ahead to guess (prefetch radius and look-ahead). It is a trade
between sharpness and freezes, and you set it.

OrthoStudio XP Live has no wait to manage, and X-Plane itself says how sharp it wants each texture:

| AutoOrtho setting | In OrthoStudio XP Live |
|---|---|
| Minimum zoom | Not needed: the far view is the 256 px level X-Plane reads when it loads an area. |
| Maximum zoom, zoom near airports | Optional caps (`--max-zoom`, `--max-zoom-airports`) to save bandwidth. By default, as sharp as X-Plane will display, where it will display it. |
| Zoom by flight level | Not needed: X-Plane already reads less sharp when the ground is farther away, and OrthoStudio XP Live follows it. |
| Prefetch radius, look-ahead, interval | Built in and measured: 300 km around, 5 minutes ahead, the rings with a 25 % margin, and 25 km more for external views. |
| Maximum wait, time budget, fallbacks | Not needed: X-Plane waits only while a flight loads, until everything it reads there is built. In flight it never waits. A texture that comes late is shown at the sharpness X-Plane already has and gets sharper at the next ring. |
| Cache sizes, workers, pipelines | Internal. |

What you choose: the imagery source (Bing by default), a zoom cap if you want to download less,
and how much disk the textures and the downloaded imagery may take.

## Where everything is, and the disk

Live has a folder of its own: `~/.orthostudio/live`, unless its settings choose another, on an APFS
disk. Not OrthoStudio XP's data folder, which may move to another disk for a while: a pack following
it would be set up again there, and what it held left behind. A folder whose disk is not plugged in
is never made on the computer's own disk instead: Live says so and does not start.

- `pack`: the scenery X-Plane reads (linked into Custom Scenery as `z_osxp_live` while OrthoStudio
  XP Live runs). Every texture of the region is there: built for real where you have flown, a
  neutral placeholder that takes no space elsewhere. (A disk-usage tool counts every placeholder
  whole, terabytes of them: they share one file's blocks.)
- `chunks.sqlite`: the downloaded imagery, so that nothing is downloaded twice.

Both have a budget, set in the window (20 GB and 10 GB by default; `--disk-budget-gb` and
`--cache-budget-gb` in a terminal, where the window's settings are the defaults). Over it,
OrthoStudio XP Live makes the textures seen longest ago coarser, only those more than 300 km from the
aircraft and the camera, where X-Plane reads nothing: first back to 256 px (their small levels are
in the file, nothing to download), then to the placeholder; and it drops the imagery downloaded
longest ago. A texture that comes back within reach is built again before X-Plane wants it.
Measured: 158 textures made coarser in 0.3 s, 0.87 GB given back to the disk.

## What it costs

- Setting up a region: about 3 minutes, once.
- A new area: 30,000 to 50,000 image pieces before the first flight there, with the 25 km for
  external views. That is one to two minutes, and a few seconds for later flights. Without the
  external views (`--camera-reach-km 0`), it is 11,000 pieces around Marseille, about 40 seconds.
- Download: from Marseille to past Mont Ventoux at FL240, texture quality Maximum, about 80,000
  image pieces (1.4 GB). Downloads run at about 450 pieces a second.
- Imagery is kept (`live/chunks.sqlite`) and never downloaded twice.

## If something goes wrong

- If OrthoStudio XP Live stops, X-Plane goes on flying over what is on the disk.
- If a texture comes late, X-Plane shows it a little blurry and sharpens it at its next ring. It
  never freezes waiting for it.
- Stop OrthoStudio XP Live after quitting X-Plane: it takes its scenery and its plugin out of X-Plane
  when it stops, and if X-Plane is still running it waits for it to quit first.
- If the streamer stops on an error, the window (or the terminal) says so at once, with the error,
  and the plugin lets X-Plane go on and says so in X-Plane's Log.txt.
- The plugin is compiled on your Mac the first time, which needs Xcode's command-line tools
  (`xcode-select --install`). Without them, OrthoStudio XP Live says so and works without the wait.

## Not yet

- The 25 km for external views is always kept, cockpit or not. The plugin could tell which view is
  in use and widen it only when needed, to download less at cruise.

- The look-ahead follows the current track; a flight plan (SimBrief) would let it look further.
- Distances are measured along the ground; counting the altitude, as X-Plane probably does, would
  download less at cruise.
- Only the AutoOrtho region set up (Europe so far).
