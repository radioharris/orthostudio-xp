# How OrthoStudio XP Live works

A guide for users: what OrthoStudio XP Live does while you fly, what you do, and why it asks for so
few settings. For the design and the measurements behind it, see [specs/live.md](specs/live.md) and
[specs/live/](specs/live/).

OrthoStudio XP Live comes with OrthoStudio XP from version 1.1, in beta, switched on. To switch
it off, put this in `live.toml`, in OrthoStudio XP's own folder (`~/.orthostudio` on macOS and
Linux, `%USERPROFILE%\.orthostudio` on Windows):

```toml
enabled = false
```

It runs on macOS (Apple Silicon and Intel Macs), Windows and Linux. It has flown with X-Plane on a
Mac and on a Windows PC (a Shadow PC, 2026-10-02), not yet on Linux. From a checkout of the
repository, `uv run python -m orthostudio.live <command>`: `ui` opens its window; the other commands
do the same from the terminal.

## One app with OrthoStudio XP

Live and OrthoStudio XP are one app. At its first start the app asks what you want to do: fly now,
with Live, or build your tiles, with OrthoStudio XP. It remembers the answer, unless you untick the
box, and opens that window at each start after; Live's settings (When the app starts) change it.
Each window has a button to the other, which brings it back when it is open already: Live in
OrthoStudio XP's top bar, its dot saying whether Live streams, and Build tiles in Live's. Quit, at
the right of Live's top bar, asks as OrthoStudio XP's does, and Cmd+Q asks the same: a stream stops,
and Live stays until X-Plane has quit. Where a computer cannot show the app's own window (a Linux
without its libraries), both open in the browser, and Live stops by itself five minutes after its
page was closed, unless it streams.

## What it is

Aerial imagery under your aircraft, anywhere in the region you fly, downloaded while you fly, as
AutoOrtho does: no tiles to build beforehand. The difference is where it sits. AutoOrtho stands
between X-Plane and its textures: X-Plane asks for a texture and waits while AutoOrtho downloads it.
OrthoStudio XP Live stands beside X-Plane: it downloads the textures before X-Plane asks for them and puts
them on the disk as ordinary files. X-Plane never waits for it, so there is nothing to freeze on.

## What you do

The OrthoStudio XP Live window opens with the app, once you chose to fly, or with the Live button
of OrthoStudio XP's window (`uv run python -m orthostudio.live ui` from a checkout). It has two
tabs, as OrthoStudio XP's screens:

- **Live**: laid out as OrthoStudio XP's Plan: the map on the left, large, and on the right the
  state, the imagery source (chosen first and saved as soon as chosen), the departure to prepare
  and Start Live, then the flight; under them the textures on a line; what Live needs (X-Plane, a
  mesh, AutoOrtho not running, the plugin), with a button to the regions where a mesh is missing;
  its log at the bottom. The source changes
  only while Live is stopped, and textures already built keep their photos: "From scratch" builds
  them all again with a new source.
- **Settings**: the regions of the mesh, the roads, forests and buildings, the zoom and what a start
  waits for, the flight plan, Live's one disk space, shared by the textures and the downloaded imagery (with
  what each takes now, and a button to delete either), and Live's folder. They apply with Save, at
  the next start; leaving the tab with answers not saved says so.

At the foot of the window, as at the foot of OrthoStudio XP's page: `Aa` sets the size of the
page's text (100, 115 or 130 %) and `− 100 % +` zooms the whole page (also Ctrl+plus, Ctrl+minus,
Ctrl+0, or Cmd on a Mac). Each window keeps its own, and its own place on the screen.

As in OrthoStudio XP's window, a field over the settings keeps only the questions holding the words
typed (accents, case and plurals aside), and the window has OrthoStudio XP's find bar (Cmd+F,
Ctrl+F), which looks in the tab shown, and text size (Cmd+plus, Cmd+minus, Cmd+0), which it takes
as they are. In a browser, the browser's own keys stay.

### The mesh, by region

Live lays its photos on sceneries: the mesh, the ground's shape and the textures' outlines. It comes
in six regions, which you install once, the ones you fly over, and delete whenever you like: Europe
(17 GB), North America (26 GB), South America (9 GB), Africa (12 GB), Asia (33 GB), Australia and
the Pacific (6 GB). The Settings tab's first question lists them: Install, or Installed and Delete.
Once unpacked a region takes about 1.7 times its size on the disk. An install goes on where it
stopped (a dropped connection, the Stop button, the Mac put to sleep), and a region is used from
the next start once it is whole. A region already on the disk (AutoOrtho's installer put the same
ones in X-Plane's Custom Scenery) is listed as installed, used as it is, and can be deleted the
same way. In a terminal: `python -m orthostudio.live regions list`, `regions install eur`,
`regions remove eur`.

Where no region installed has a mesh, X-Plane shows its own scenery there, without photos: Live
says so, in its window and in a small panel in X-Plane ("no mesh here: install North America"),
from the start of a flight there or once the aircraft flies there. Over the open sea, where no
region has any mesh, it says nothing. What the plugin shows in X-Plane is in the language of the
Live window (its EN/FR selector).

They come from github.com/kubilus1/autoortho-scenery, where AutoOrtho's author publishes them and
AutoOrtho downloads them. Live downloads them from there, and never publishes them anywhere else:
that repository names no licence.

### The map

The window has a map: the photos of your imagery source, the borders and airports as in
OrthoStudio XP's Plan, and over them what Live has done. The aircraft moves on it while you fly,
pointing along its heading as in X-Plane, with the camera's place in external views; the map follows it until
you drag it away ("Follow the aircraft" brings it back). Your SimBrief route is drawn while Live
follows it, and before a flight the "Show my SimBrief route" button asks SimBrief for it. The
regions installed are shaded while zoomed out, and the textures already built are coloured by how
sharp they are on the ground, which is the zoom of their photos: ZL19 violet (about 20 cm a
pixel), ZL18 blue (40 cm), ZL17 green (85 cm), ZL16 amber (1.7 m), ZL15 orange, grey blurrier,
within 3 seconds of being built while Live runs; zoomed out, the many blurry ones are grouped by
square. The mesh's own tiles have a zoom too, which says only how big they are: a ZL16 tile is
6.8 km wide, an airport's ZL18 tile 1.7 km. Filled with ZL17 photos, a ZL16 tile takes 8192
pixels; filled with ZL18 photos, an airport's tile takes 4096, and is the sharper. Point at a
texture and a bar over the top of the map says what it is, live: its photos and how much ground
a pixel shows, its tile, its pixels and the disk it takes, and when Live last had it in reach.
The "Details" box hides the bar.

Your own tiles are framed as OrthoStudio XP's Plan frames the tiles in X-Plane: green for a tile
OrthoStudio XP built, violet for one imported from Ortho4XP, grey for another scenery's ground.
X-Plane shows them there, so the map leaves Live's textures out under them, and the bar says so;
the checks list them too. Live finds them in X-Plane's list of sceneries, above its own pack (or
where it would go, while Live is stopped), and looks again when that list changes.

### Roads, forests and buildings

Where Live shows its photos, X-Plane no longer draws its own roads, trees, buildings and beaches:
it reads one ground per square, and its own scenery carries them. Each region brings them back as
"overlays", which Live puts on its squares, in a pack of its own listed just above the ortho
(`y_osxp_live_overlays`), except:

- where simHeaven X-World is: it brings its own roads and buildings, and its manual asks to leave
  out what is not needed, since X-Plane loads a pack even when another hides it. Looked at square
  by square: simHeaven X-World Europe has nothing over North Africa, which gets the region's;
- on your own tiles, which have their own overlays, and where another overlay pack is on
  (AutoOrtho's `yAutoOrtho_Overlays`, Ortho4XP's): they would be drawn twice.

Global Forests needs nothing: it hides the forests of every pack below it, Live's included, and
keeps their roads and buildings. The setting "Never" leaves the overlays out altogether.

There is no setting for the texture quality or for how far outside views reach: Live reads the
texture quality X-Plane is set to, and builds sharp 10 km beyond the aircraft for outside views
(since 2026-09-29, after a test flight: 20 km before, 25 km until 2026-09-28). Less, and X-Plane
could show the neutral colour, green-grey, around a distant camera.

The zoom is set as in AutoOrtho: a maximum zoom (14 to 17, 16 by default) and a zoom near airports
(16 to 19, 18 by default, for the package's ZL18 textures, which are its airports' surroundings).
X-Plane picks by itself, by distance, which version of a texture it shows; the zoom sets the
sharpest version there is, the one seen up close. Below the package's zoom a texture is smaller
(1024 px at ZL14): less sharp near the ground, far less to download, shorter waits at a start. One
zoom above, a texture is 8192 px: sharper near the ground only, around you. A lower zoom is blurrier
within 38 km, the ground under the aircraft at cruise too. A new zoom applies at the next start of
Live, X-Plane closed: Live puts every texture at its new size, about two minutes for a whole
region, keeping what it built and downloading nothing.

How the 8192 px work, with ZL17 or ZL19. Each texture is a file holding the same photo at several
sizes, stacked like floors: 8192, 4096, 2048, 1024 px and so on. Its label, at the top, says the
size of the largest floor. X-Plane reads the label when it loads the area, about 200 km away, and
keeps it: a label changed afterwards is often not read. Until 2026-09-28 Live made its textures
4096 px and changed them to 8192 px ahead of the aircraft, and X-Plane showed their 8192 px only
now and then. So every file's label says 8192 px from the start, and never changes.

Coming nearer, X-Plane reads the floors counting from the top: the fifth at load, the fourth at
72 km, the third at 38 km, the second at 21 km, the top one at 12 km. Live puts the photos in each
floor at the same distances as for a 4096 px file, so nothing more is downloaded there. With an
8192 px label X-Plane reads each floor a ring earlier. Nearer than about 40 km that changes
nothing: the margin kept for external views has the photos there first. Farther, at 72 km and at
load, it finds the photos of the floor below, copied at its size: the same image, which the
screen could not show sharper at that distance anyway. The top floor gets its own photos, one zoom
above the package's, within 15 km of the aircraft, of its path ahead and of the camera, in every
direction, before X-Plane reads it at 12 km: a turn or a go-around needs nothing guessed. High up
there are fewer, since X-Plane measures in three dimensions. If those photos are late (a slow
connection), Live copies the 4096 px floor into the top one first: the ortho as with ZL16, never
blank. A copy takes four times the disk of the floor it copies, so there are no others: with a
copy in every floor, a five-minute flight near Lyon took 27.7 GB (2026-09-28). An external camera
taken more than 15 km from the aircraft and near the ground may find the neutral colour, green-
grey, under it until it goes away. For an airport's textures, the 12 and 15 km are 3 and 3.75 km.

Each 8192 px texture is made in a process of its own, which ends with it and gives its memory
back: made by the twelve workers that make the others, they kept 14 GB between them, and the Mac,
short of memory, froze X-Plane for up to a second (2026-09-28).

When a flight starts, X-Plane waits on its loading screen for the textures around the aircraft,
and with ZL17 or ZL19 for their 8192 px within 15 km. Nothing to choose: the three start choices
of before (full, 8192 px around the aircraft, normal) went with the textures that grew.

What that downloads, estimated on the real textures around Lyon, flying at 250 kt (about 20 KB a
piece of imagery): with ZL16 and 18, about 1 GB at the start and 7 GB an hour; with ZL17 and 19,
about 1.4 GB at the start and 13 GB an hour.

The eye hardly tells ZL17 from ZL16, so a test shows where X-Plane really shows the 8192 px: with
"Test: show the 8192 px textures in pink" ticked (or `--tint-8192`), Live builds the 8192 px level
of every texture washed with pink, a copy and the 4096 px and smaller levels as they are.
Where the ground turns pink, X-Plane shows the 8192 px: around the aircraft from the start, and
within 15 km of it in flight. It showed that X-Plane shows the 8192 px of the files it loads, and
that its reload of the scenery does not read again the textures it holds. Live's tab says the
test is on. After it, untick the box and delete the textures (the photos kept): they are built
again normally, with nothing to download.

The imagery sources are OrthoStudio XP's: Bing Maps, Esri, EOX, the countries' own (Spain's PNOA,
the Netherlands' PDOK...), and the sources you added in OrthoStudio XP's Plan. Live adds two that
AutoOrtho offers: Esri's Firefly (the same photos as Esri, softer colours) and NAIP (the United
States). Google, Yandex and Apple are not offered: their terms forbid taking their photos outside
their own applications. A country's source has nothing past its border: there, and wherever it has
no imagery, Live takes Bing Maps. Textures already built keep their imagery; delete them to build
them again from another source.

Your own tiles come first. Where you have built a tile with OrthoStudio XP (or imported one from
Ortho4XP, or installed another ortho scenery above Live's pack), X-Plane shows it, and Live
downloads nothing there: at a start from your home airport, X-Plane waits for none of it. Live
reads X-Plane's list of scenery packs at each start and says in its log which squares it leaves to
your own; OrthoStudio XP lists the tiles it builds above Live's pack by itself.

The first start prepares every region of AutoOrtho you have installed (Europe, North America...),
once, in about 2 minutes a region, so that X-Plane always finds every file it looks for. It takes
almost no disk: the files share their content with AutoOrtho's until they are filled in. A region
you install later is added at the next start, keeping everything already built.

Then, for every flight:

1. Click Start in the window (or `uv run python -m orthostudio.live stream` in a terminal). It
   builds nothing yet: nothing says where you will fly, so it waits for the flight you choose.
   "From scratch" forgets every texture built and all imagery downloaded, as the very first time.
2. Start X-Plane (the window's Start X-Plane button, once Live is ready), choose your flight and
   click Start Flight. X-Plane then waits on its loading
   screen until the ortho around your departure is built, as it does with AutoOrtho. That takes a
   few seconds where you have flown before, and a minute or two the first time in a new area. The
   Live window counts down the textures still to build, and so does a small panel over X-Plane,
   with the seconds gone by and the download rate in MB/s. X-Plane's own window looks frozen
   meanwhile: it is waiting, it has not crashed.
3. Fly, anywhere. Another flight started from X-Plane's menu waits the same way.
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
  shows the neutral colour where nothing was built, until it loads that place again (a new
  flight there, or X-Plane restarted). Live asks for no reload of the scenery: X-Plane's keeps
  the textures it holds.
- Live answers the plugin at once even while it is busy: the plugin lets X-Plane go when a
  question gets no answer within a second, and a busy Live let X-Plane start at LGAV and LCLK
  with nothing built (2026-09-29).
- In flight, the plugin asks the streamer every second whether it has a notice for you (no mesh
  under the aircraft: the region to install), and shows it in a small panel with its cross.
- While X-Plane flies, Live encodes at most four textures at once, at a low priority: X-Plane's
  threads always go first (a user saw stutters while Live built). While X-Plane waits on its
  loading screen, every encoder works. The plugin measures X-Plane's frames and says the longest
  of each second; Live logs a frame of 100 ms or more as a stutter, with what it was doing then.
- Live asks the imagery source for as many pieces at once as its servers take, as OrthoStudio XP
  measured them: 128 for Bing Maps. A piece that has not come within 10 s is replaced meanwhile by
  the imagery of the zoom below (a slightly blurrier square), so that X-Plane never waits longer
  for it; Live asks for it again later and builds the texture again once it comes.

Without the plugin (`--no-plugin`), X-Plane does not wait. Where a texture is not built yet, it shows
a plain placeholder colour (never black), until it loads that place again: X-Plane's reload of the
scenery keeps the textures it holds, so Live asks for none.

## What happens while you fly

Every two seconds, OrthoStudio XP Live reads where the aircraft is, where it is heading and how fast (as
AutoOrtho does, over X-Plane's network data), and where the camera is. It looks five minutes ahead
along the track, and for every texture within 300 km it works out how sharp X-Plane will want it
when the aircraft, or the camera, passes closest. External views count: X-Plane measures from the
camera, so a chase or free camera far from the aircraft gets its textures sharp too. The distances
follow X-Plane's image: on a wider screen, or zoomed in (a narrower field of view), X-Plane reads
sharp textures farther, and Live builds them farther too. The Flight card shows your image and how
far X-Plane reads 4096 px there.

With your SimBrief name in the settings (Flight plan), Live follows your last SimBrief plan while
you fly it, ten minutes of route ahead, turns and approach included, beside the five minutes
straight on; off the route, or flying it the other way, it looks straight on only. Only that name
goes to simbrief.com: when a flight starts, and while the Live window is open (when it opens, when
you come back to it, and every two minutes), for the "Prepare my departure" box. The box takes the
departure of your last plan; when you make a new plan on simbrief.com, the window says "Flight
plan updated" and changes the box only when you click "Load it" (you may have typed another
departure). The small arrow after "From your last SimBrief plan" asks SimBrief at once, without
waiting for the next look. The route is drawn on the map only while you fly it (or when you ask for it with the
map's button). The window offers the name OrthoStudio XP has, and asks SimBrief nothing until you
take it. What is
missing is downloaded and built, the most urgent first, and replaces the file on the disk. X-Plane
picks the new file up by itself the next time it wants that texture sharper.

External views need more than that. With Shift+4 the camera can go kilometres away in a second,
much faster than anything downloads. X-Plane reads whatever is in the files there at once, and it
does not read a level again once it has it. So OrthoStudio XP Live keeps the sharp levels built 10 km
farther around the aircraft than the aircraft alone needs, in the cockpit too
(`--camera-reach-km`), and an external
view shows the right imagery as soon as you switch to it, as with AutoOrtho. A camera taken farther
than that may find a placeholder, which X-Plane keeps until it loads that area again. The 8192 px
level has no reach: 15 km around the camera wherever it is.

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
| Prefetch radius, look-ahead, interval | Built in and measured: 300 km around, 5 minutes ahead, the rings with a 25 % margin, and 10 km more for external views. |
| Maximum wait, time budget, fallbacks | Not needed: X-Plane waits only while a flight loads, until everything it reads there is built. In flight it never waits. A texture that comes late is shown at the sharpness X-Plane already has and gets sharper at the next ring. |
| Cache sizes, workers, pipelines | Internal. |

What you choose: the imagery source (Bing by default), a zoom cap if you want to download less,
and how much disk the textures and the downloaded imagery may take.

## Where everything is, and the disk

Live has a folder of its own: `~/.orthostudio/live`, unless its settings choose another, on the
computer's own disk or on a disk of the same kind: APFS on a Mac, NTFS on Windows, ext4 on Linux,
never Mac OS Extended, exFAT or FAT32, which would take every texture round the aircraft whole.
Not OrthoStudio XP's data folder, which may move to another disk for a while: a pack following
it would be set up again there, and what it held left behind. A folder whose disk is not plugged in
is never made on the computer's own disk instead: Live says so and does not start. On Windows,
Live keeps its folder out of Windows Search (a rule it adds itself, as the Indexing Options do):
listing its hundreds of thousands of files kept the indexer busy for whole flights.

Microsoft Defender, on Windows, checks each new file Live writes: at Live's first start on a
Windows PC it took about one core for a while (2026-10-02). Live does not take its folder out of
Defender itself: that needs an administrator's yes, and it is a security setting for you to choose.
To spare Live's start that check, add its folder as an exclusion: Windows Security, Virus & threat
protection, Manage settings (under Virus & threat protection settings), Exclusions, Add or remove
exclusions, Add an exclusion, Folder, then `%USERPROFILE%\.orthostudio\live`, or the folder Live's
settings chose. Defender then leaves alone the photos Live downloads and the textures it makes
there, and nothing else.

- `pack`: the scenery X-Plane reads (linked into Custom Scenery as `z_osxp_live` while OrthoStudio
  XP Live runs). Every DSF of the region is there; the files they name only round the aircraft,
  made as X-Plane comes: each texture built for real where you have flown, a neutral placeholder
  that takes no space elsewhere. (A disk-usage tool counts every placeholder whole: they share one
  file.)
- `chunks.sqlite`: the downloaded imagery, so that nothing is downloaded twice.
- `regions`: the AutoOrtho regions Live installed (`z_ao_<region>` for the mesh, `y_ao_<region>`
  for its overlays), and while an install runs, what it has downloaded so far. In `regions/.lists`,
  the list of each region's files, AutoOrtho's regions too, so that a start does not read the
  whole region again: made at the end of a region's install, by the Windows setup ("Reading the
  regions of OrthoStudio XP Live..."), or as Live's window opens.
- `overlays`: the roads, forests and buildings of Live's squares (linked into Custom Scenery as
  `y_osxp_live_overlays` while Live runs), copies of the regions' that take no space.

They share one disk space, as AutoOrtho's and XPME's cache is one: set in the window (40 GB by
default; `--disk-gb` in a terminal, where the window's setting is the default), half for the pack's
textures, half for the imagery (two spaces, set apart, left a user wondering which to raise). Over
its half, OrthoStudio XP Live makes the textures seen longest ago coarser, only where nothing can
show it: first back to 256 px (their small levels are in the file, nothing to download) where
X-Plane no longer holds them, then to the placeholder more than 300 km away; and it drops the
imagery downloaded longest ago. X-Plane holds a window of 4 by 3 scenery tiles round the aircraft
and lets go of the tiles it leaves behind, about 130 to 210 km behind the aircraft in France: a
measurement flight showed it, and Live keeps its hands off that window. A texture that comes back within reach is built again before X-Plane wants
it. Measured: 158 textures made coarser in 0.3 s, 0.87 GB given back to the disk.

## What it costs

- Setting up a region: about 3 minutes, once.
- A new area: 30,000 to 50,000 image pieces before the first flight there, with the 25 km for
  external views of then (20 km since 2026-09-28). That is one to two minutes, and a few seconds for later flights. Without the
  external views (`--camera-reach-km 0`), it is 11,000 pieces around Marseille, about 40 seconds.
- Download: from Marseille to past Mont Ventoux at FL240, texture quality Maximum, about 80,000
  image pieces (1.4 GB). Downloads run at about 450 pieces a second.
- Imagery is kept (`live/chunks.sqlite`) and never downloaded twice.

## If something goes wrong

- OrthoStudio XP Live keeps its log in `LogLive.txt`, at the top of your X-Plane folder, beside
  X-Plane's own `Log.txt`: the one to send when something went wrong. It is written again each
  time Live's window opens, as X-Plane's is, so send it before opening Live again. The ten before
  are kept in the `log` folder of Live's folder. The window's Log shows where it is, with a button
  that shows it in the Finder (File Explorer on Windows).
- If OrthoStudio XP Live stops, X-Plane goes on flying over what is on the disk.
- If a texture comes late, X-Plane shows it a little blurry and sharpens it at its next ring. It
  never freezes waiting for it.
- Stop OrthoStudio XP Live after quitting X-Plane: it takes its scenery and its plugin out of X-Plane
  when it stops, and if X-Plane is still running it waits for it to quit first.
- If the streamer stops on an error, the window (or the terminal) says so at once, with the error,
  and the plugin lets X-Plane go on and says so in X-Plane's Log.txt.
- The plugin comes with OrthoStudio XP's installer. Run from a checkout of the repository on a
  Mac, OrthoStudio XP Live compiles it the first time, which needs Xcode's command-line tools
  (`xcode-select --install`). Without them, it says so and works without the wait.

## Not yet

- The camera's reach is kept in the cockpit too: an external view comes without warning, and
  X-Plane reads a texture once (widening it only in external views was tried and undone on
  2026-09-29). It is 10 km since a test flight that day, where nothing was seen: Live logs when
  a camera gets ahead of it ("camera ahead of Live") and keeps the reach each run's camera
  needed in `pack/_live/camera.json`, to set it again on how pilots really fly.

- Distances are measured along the ground, but for the 8192 px level: counting the altitude
  at every level would download little less at cruise, where most is for external views (the
  camera's reach around), which a shorter reach at cruise would save. To set it on how pilots really fly, Live
  notes how far and how low external views take the camera, at cruise and below, says it in its
  log when it stops, and keeps the last 20 flights in `pack/_live/camera.json`.
- Only where AutoOrtho has a mesh: the six regions cover the land of the planet, the islands AutoOrtho
  left out excepted.
