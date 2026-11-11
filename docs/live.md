# OrthoStudio XP Live

Aerial imagery under your aircraft wherever you fly, downloaded while you fly, with no tiles to
build first. OrthoStudio XP Live comes with OrthoStudio XP from version 2.0.

![Live's window in flight near Toulouse: on the left the map, the aircraft at its centre and the textures built around it coloured by how sharp they are, sharper still around the airports; on the right Live's state, the photo source and its zoom, the flight's altitude, speed and position, and the textures being built](images/live/flight.png)

This guide is for pilots: what Live does, what you do, and why it asks for so few settings. The
design and the measurements behind it are in [specs/live.md](specs/live.md) and
[specs/live/](specs/live/).

## Contents

- [What it is](#what-it-is)
- [Compared with XPME and AutoOrtho](#compared-with-xpme-and-autoortho)
- [Getting started](#getting-started)
- [A flight, step by step](#a-flight-step-by-step)
- [The window](#the-window)
- [The zoom](#the-zoom)
- [The season](#the-season)
- [Ground grain up close (decals)](#ground-grain-up-close-decals)
- [Towns at night](#towns-at-night)
- [Imagery sources, your own tiles, roads and forests](#imagery-sources-your-own-tiles-roads-and-forests)
- [The mesh, by region](#the-mesh-by-region)
- [What happens while you fly](#what-happens-while-you-fly)
- [Why there are no zoom by flight level, prefetch or waiting settings](#why-there-are-no-zoom-by-flight-level-prefetch-or-waiting-settings)
- [Where everything is, and the disk](#where-everything-is-and-the-disk)
- [If something goes wrong](#if-something-goes-wrong)
- [Limits](#limits)

## What it is

Live works as AutoOrtho and XPME do from the pilot's seat: you choose a flight anywhere, and the
ground is aerial imagery. The difference is where it sits.

- **No virtual disk.** AutoOrtho and XPME put a virtual disk between X-Plane and its textures (FUSE,
  or an NFS server on a Mac): X-Plane asks for a texture and waits while they download and build
  it. Live stands beside X-Plane: it builds the textures before X-Plane asks for them and puts them
  on the disk as ordinary files. There is nothing to install for it, no kernel extension, no
  driver.
- **X-Plane never waits while you fly.** It waits only while a flight or a new location chosen in
  flight loads, until the ground it shows first is built. In flight a texture that comes late is shown a little blurry,
  and sharper at the next look.
- **If Live stops, X-Plane flies on** over what is on the disk. Nothing is pulled from under it.
- **Light on the computer.** Live took about a third of one processor core in flight, a quarter of
  what XPME took on the same flight (measured below).
- **Faithful to the photos.** The textures are the photos as the source delivers them, without an
  added sharpening.

At the first start the app asks what you want to do: build your tiles, with OrthoStudio XP, or
fly now, with Live. Both are in one window, and you can go from one to the other at any time.

![The app's first question: Build my tiles, with OrthoStudio XP, or Fly now, with OrthoStudio XP Live, each with what it gives and what it costs](images/live/welcome.png)

## Compared with XPME and AutoOrtho

The same flight with each program, on the same Mac (Apple M4 Pro, 48 GB), on 8 and 9 October
2026: X-Plane 12 flown by OrthoStudio XP's test pilot along the same path, nothing downloaded
beforehand (empty caches), Bing Maps photos at ZL16 and ZL18 near airports: AutoOrtho with its
default settings, XPME at its "Normal resolution" with its user's other settings, Live with its
defaults. Around Toulouse, 20 minutes low over
the plain; then over the Pyrenees, 18 minutes from high ground down a valley (XPME 4.7.23 and Live
only).

| | XPME | OrthoStudio XP Live | AutoOrtho |
|---|---|---|---|
| Start, Toulouse: from the flight starting to load to its first image | 125 s | 56 s | 297 s |
| then frozen before flying | 77 s | 4 s | 41 s |
| Start, Pyrenees: load to first image, then frozen | 112 s, then 42 s | 45 s, then 4 s | |
| Processor in flight (cores, median) | 1.27 (Pyrenees 1.25) | 0.32 (Pyrenees 0.37) | 0.57 |
| Program's memory in flight, median / most | 2.5 / 6.7 GB | 2.9 / 4.8 GB | 3.8 / 5.3 GB |
| X-Plane's frame rate, Toulouse | 57.2 fps | 57.2 fps | 57.3 fps |
| X-Plane loading scenery in flight, Toulouse | 58 s and 44 s | 24 s and 20 s | one of 278 s |
| Downloaded, start / flight, Toulouse | 0.69 / 1.31 GB | 0.60 / 1.18 GB | 2.29 / 1.86 GB |
| Written to the disk, start / flight, Toulouse | 1.92 / 3.81 GB | 2.54 / 4.19 GB | 3.92 / 4.03 GB |

What the figures say:

- **The start.** X-Plane's own opening takes about 25 s with each. After it, Live had the ground
  ready about twice as fast as XPME and five times as fast as AutoOrtho, and X-Plane flew 4 s after
  the first image, where it stayed frozen 41 to 77 s with the others. With "Prepare my departure
  before starting the flight" (below), Live builds the start before X-Plane loads it, and X-Plane
  waits for nothing.
- **In flight**, the frame rate is the same with all three: none of them slows X-Plane down. When
  X-Plane loads the scenery of a new area, it takes half as long with Live, whose files are already
  on the disk.
- **The processor**: Live a third of a core, XPME more than one.
- **The disk**: Live writes more than XPME (over the Pyrenees, 8.3 GB against 4.7), its weak point:
  each texture is written whole as it gets sharper.
- **Sharpness**: on the same views, Live's screenshots measured 3 % less fine detail than XPME's,
  and AutoOrtho's 4.8 %. XPME sharpens its textures (3.7 to 6 % more detail than the photos
  themselves, and more contrast), where Live keeps the photos as they are.

One flight each: the large gaps (the start, the processor, the scenery loads) are clear, the gaps
of a few percent (sharpness, memory, writes) would want more flights before being taken as rules.
How the flights are made and measured: [specs/live/flights.md](specs/live/flights.md).

## Getting started

1. **Install the mesh of where you fly.** Live lays its photos on a mesh, the ground's shape, in six
   regions: Europe, North America, South America, Africa, Asia, Australia and the Pacific. Settings,
   first question, Install (see [The mesh, by region](#the-mesh-by-region)). A region AutoOrtho's
   installer already put in X-Plane is found and used as it is.
2. **Choose the photos and the zoom**, at the top of Live's tab: the photo source (Bing Maps by
   default), the zoom away from airports and the zoom around them (ZL16 and ZL18 by default).
3. **Optionally, your SimBrief name** (Settings, Flight plan): Live then follows your plan in
   flight, and offers your last plan's departure to prepare. Only that name goes to simbrief.com:
   at each flight's start, and while Live's window is open (as it opens, when you come back to it,
   and every two minutes).

The first start then prepares the region for X-Plane, once, in a few seconds (up to a minute on a
slow disk).

## A flight, step by step

1. **Prepare my departure before starting the flight** (optional): type the airport (or `lat,lon`)
   and click Prepare. Live builds the ground around it now, before X-Plane loads the flight:
   X-Plane then passes its loading screen at once, and the map goes there. With your SimBrief name,
   the box already holds your last plan's departure ("Last SimBrief plan"); a newer plan made on
   simbrief.com is offered with "Load it". Prepare starts Live if it is not running yet: skip the
   next step then.
2. **Start Live.** To start as the very first time, the trash at the end of the buttons deletes
   the textures built, the photos downloaded, or both, after asking, as Settings' Delete does.
3. **Start X-Plane** (the button beside Live's), choose your flight and click Start Flight. X-Plane
   waits on its loading screen until the ground around your departure is built: a few seconds
   where you have flown before, a minute or two the first time in a new area. Live's window and a
   small panel over X-Plane count down the textures left, with the download rate, or « every photo
   in, building » once only the building is left. X-Plane's own window looks frozen meanwhile: it is
   waiting, it has not crashed.
   ![Live preparing a departure from Munich: its state says it builds the ortho around EDDM, with a bar, the textures left, the seconds gone by and the download rate](images/live/departure.png)

4. **Fly**, anywhere. Another flight started from X-Plane's menu waits the same way. A new location
   chosen in flight from the Flight Configuration screen waits too: X-Plane stays paused on that
   screen, with Live's own loading window over it, the same as the plugin's, and goes on by itself
   once the ground there is built. On a Mac the pointer over X-Plane may turn into the spinning
   wheel meanwhile; on Windows X-Plane is not greyed.
5. **Quit X-Plane, then Stop.** Live takes its scenery and its small plugin out of X-Plane. Stopped
   while X-Plane still runs, Live waits for X-Plane to quit first ("Leave it installed" stops
   waiting).

## The window

Live and OrthoStudio XP are one app, in one window. At the top, Build | Live shows one side or the
other, with what the other is doing in a word beside its name (build under way, in flight), and
the tabs of the side shown. The language, the theme, Quit, and at the foot the text size and the
zoom, are there once for both. A build started while Live streams, or a flight while a build runs,
asks first: a build takes a lot of processor time and may slow X-Plane down.

**Live's tab** is laid out as OrthoStudio XP's Plan: the map on the left, as tall as the screen,
and on the right:

- **The state**: Ready, Starting, X-Plane waiting with a countdown, Following the aircraft, with
  what to do next. Under it the photo source and its zoom on one line (chosen before Start,
  locked while Live runs, what each choice does in its hover), the departure to prepare, and Live's
  buttons: Start Live, Start X-Plane, Stop, and a trash at their end to delete what Live keeps.
- **Flight**: whether X-Plane sends its position; the altitude, the ground speed and the position;
  how far X-Plane's image is sharp (from your screen's width and field of view), the SimBrief plan
  while the aircraft follows it, the camera when it is away from the aircraft, and the departure.
- **Textures**: what they are doing (all built, building, late, downloads refused or failing) and
  the download rate; four boxes: to build (and how many are urgent), being built, built since the
  start, downloaded; the textures built by size; Live's disk space.
- Under them, the **checks**: what Live needs (X-Plane, a mesh under where you fly, no other program
  streaming ortho, its plugin), with a button to the regions where a mesh is missing; and the
  **log**.

**The map** shows the photos of your source, the borders and airports, the aircraft along its
heading (and the camera in external views, a dashed line to it with the distance from the aircraft
to the ground under it, its height in it, as X-Plane counts it to choose the textures' levels), your
SimBrief route while you fly it (or with "Show my
SimBrief route"), and the textures already built, coloured by how sharp they are on the ground:
ZL19 violet (about 20 cm a pixel), ZL18 blue (40 cm), ZL17 green (85 cm), ZL16 amber (1.7 m), ZL15
orange, grey blurrier. Zoomed out, the regions installed are shaded. Point at a texture and a bar
over the map says what it is: its photos, its tile, its pixels, its disk, when Live last had it in
reach ("Details" hides the bar). Your own tiles are framed, as on the Plan. The map follows the
aircraft, and comes to it when a flight starts (about 60 km across); drag it away in flight
to look elsewhere, and "Follow the aircraft" brings it back. The next flight is followed again by
itself. Prepare brings the map to the departure, as close as a flight's start.

**The Settings tab**, the most needed first: the X-Plane folder (one setting with Build's), the
mesh's regions, Live's disk space (with what the textures and the imagery take, and a button to
delete either) and Live's folder; then what the flight shows: the roads, forests and buildings,
your own tiles, the season, the night lights, the ground grain up close; then the flight plan and
what the app opens on. They apply with Save, at the next start.

![Live's Settings: where X-Plane is installed, the mesh by region with what each downloads and an Install button, the disk space, the season and the night lights](images/live/settings.png)

## The zoom

One list, three choices: a zoom away from airports and a zoom around them (for the mesh's airport
textures), as AutoOrtho's two. X-Plane picks by itself, by distance, which version of a texture it
shows; the zoom sets the sharpest version there is, the one seen up close.

- **16/18, standard** (the default): the package's own zooms, 4096 px textures.
- **16/19, sharper airports**: 8192 px textures around airports, where you fly low; the rest as
  in 16/18.
- **17/19, sharpest**: 8192 px textures everywhere.
- **One above** (ZL17, ZL19), a texture is 8192 px: sharper near the ground only, within about
  15 km of the aircraft and of its path at ZL17 (about 4 km at ZL19 around
  airports). It takes more memory: with less than 32 GB, the
  window says X-Plane may stutter and recommends the default. Above about 18,500 ft over the ground,
  Live stops fetching those 8192 px photos, which the screen cannot show from so high, and takes
  them up again on the way down.
- **A new zoom** applies at the next start of Live, X-Plane closed, at once: each texture takes its
  new size only when X-Plane comes near it, round your departure or ahead of the aircraft, keeping
  what it built and downloading nothing. Textures where you never fly again are never touched, and
  going back to your earlier zoom before flying near them loses none of their sharp photos.

Downloads, estimated around Lyon at 250 kt: with ZL16 and 18, about 1 GB at the start and 7 GB an
hour; with ZL17 and 19, about 1.4 GB and 13 GB.

To see where X-Plane really shows the 8192 px: `tint_8192 = true` in `live.toml`, in OrthoStudio
XP's own folder (`~/.orthostudio`), builds that level washed with pink from the next start. Set it
back to `false` after, and delete the textures: they are built again, their sharpest photos
downloaded again.

## The season

The photos are mostly taken in summer. Live can give them the colours of another season
(Settings, Season), as measured on satellite photos of the same places through the year:

- **Spring**: what is green a little yellower.
- **Summer**: the photos as delivered.
- **Autumn**: what is green a little yellower and paler.
- **Winter**: what is green turns olive. No snow: white cannot be made from a summer photo
  without looking wrong.

The changes are discreet because the real ones are: most of a season's change is field by field
(a crop grown, harvested, ploughed), which no colour can guess, and in the south of France summer
is the driest season. The light of the season, a lower sun in winter, is X-Plane's own. The
sliders go further for those who want more.

**Follow the date of the flight in X-Plane** (the default): Live reads the date you chose in
X-Plane when you click Start Flight, and takes its astronomical season there: spring from 20
March, summer from 21 June, autumn from 23 September, winter from 21 December. South of the equator
the seasons are the other way round, and between the tropics the photos stay as delivered (summer).
Untick it to have always the season you choose.

Each season has five sliders: brightness, contrast, saturation, temperature (colder or warmer) and
vegetation (greener, or yellow and russet: only what is green in the photo, never fields, roofs,
roads or water). A preview shows a place you flew over, from the photos Live keeps, as delivered
and in the season; "Another place" shows another one, "Live's values" sets the season back.

The season is chosen when a flight starts and never changes in flight: X-Plane reads each texture
once. When the season changes, Live builds the textures again as you fly: the far ones from the
photos it keeps, the sharpest ones (4096 and 8192 px) downloaded again, as on a first flight there,
since Live does not keep their photos. That start takes as long as a first one. Your own scenery
keeps its colours.

## Ground grain up close (decals)

X-Plane 12's decals: a fine grain X-Plane draws over the photo seen from very low, which softens
its blur. Live writes the one chosen in its settings on the land of its squares (the `DECAL_LIB`
line of each land terrain file it places), from OrthoStudio XP's list: `maquify_2_green_key`
by default, Ortho4XP's, shrubby vegetation on the green and stony dirt elsewhere, the one
OrthoStudio XP's tiles have; `grass_and_stony_dirt_1`, long grass on the green, a finer grain;
or none. The sea keeps none. A new choice takes effect at the next start: the terrain files of the
squares flown already are made again as X-Plane comes, not while X-Plane runs.

## Towns at night

X-Plane 12's own ground glows at night where it is a town; a photo replaces that ground, and
without more a town on Live's photos is black at night but for X-Plane's roads and buildings.
With "Night lights" on (the Settings tab, on by default), Live gives each texture of its land a
night texture that X-Plane adds to the photo in the dark: street lamps along X-Plane's own roads,
orange and white, all lit in towns and villages and few in the open country, as NASA's Black
Marble sees them, and windows along the town's streets. Between the lights, the photo is as X-Plane lights it at
night.

- Nothing is made by day. From two hours before sunset in X-Plane, by its time at the aircraft,
  until sunrise, each texture Live builds gets its night texture, the sharp ones near the
  aircraft first, so that X-Plane does not find a texture without its night; X-Plane fades them
  in by itself at dusk (at Nice on 11 November: nothing at 17:25, lit at 17:50, full at 18:15).
- In flight they are made by one process of its own, at the priority of the processes that build
  the photos.
- A jump of X-Plane's clock into the night in flight (its menu, its time commands) finds the
  night textures of a city to make, and X-Plane lights them a square after another as it reads
  them: a minute or more over a large city. That is X-Plane's own pace: with every night texture
  ready in 10 s, Los Angeles lit at the same pace (2026-11-11). A start at night and a dusk in
  real time find them all made.
- A start at night waits for the night textures X-Plane reads round the airport, as it waits for
  its photos, three minutes at most once the photos are built: a team of processes makes them
  while the photos are built, and ends once the start waits for none (at LSGG, 8,685 night
  textures in about a minute; replayed on the same start, its 8 processes took 2.6 to 3.8 GB at
  most together for those seconds, as Activity Monitor counts it). Whether it is night there is known exactly once X-Plane loads the flight:
  before, Live takes the time X-Plane saved with the start, its time zone's, and counts it night
  if it may be.
- Far away they are made small first, as the photos are, so a town shows from far: the coast of
  Nice from 40 and 80 km.
- Live stays within its disk space, each file counted as the disk takes it: the night textures
  made take a part of it, those with lights only (none is made by day; those made stay on the
  disk), and X-Plane's roads kept for them 5 % of it, 1 GB at most. On a Mac and on Linux the
  night textures take about a tenth of what Live keeps at its usual zooms (ZL16 and above, a
  seventh at most), up to a quarter at ZL15 and half at ZL14; on Windows up to half,
  where the disk (NTFS) gives a far texture's file 128 KB whatever it holds, a night one as a
  photo (on a start at LSGG, every night texture lit: 11 % on a Mac, 44 % on Windows; the sea
  and the forests take nothing). With the same night textures on a tile of Nice, flown three times each way at
  2704 x 1756 on an M4 Pro, frame times did not change beyond their spread from one flight to the
  next.

"Soft", "Normal" (the default) or "Strong": the same lamps fainter or brighter. A new strength
shows from the next start: Live makes its night textures again as night comes, the nearest first.
"No": no night texture, the terrain files naming none; the night textures and the roads kept for
them are deleted. A change between on and off takes effect at the next start with X-Plane not
running, as the decal's: the terrain files flown already are made again as X-Plane comes. So does
the night itself after an update from a version without it: Live started while X-Plane runs
lights nothing until then.

## Imagery sources, your own tiles, roads and forests

**Sources.** OrthoStudio XP's: Bing Maps, Esri, EOX, the countries' own (Spain's PNOA, the
Netherlands' PDOK...), and the sources you added in OrthoStudio XP's Plan; plus two AutoOrtho
offers, Esri's Firefly (the same photos as Esri, softer colours) and NAIP (the United States).
Google, Yandex and Apple are not offered: their terms forbid taking their photos outside their own
applications. A country's source has nothing past its border: there, and wherever it has no
imagery, Live takes Bing Maps. The source's credit and licence are at the foot of the map.
Textures already built keep their photos: deleting them (the trash after Live's buttons, the photos
kept) builds them all again with a new source.

**Your own tiles first, or Live everywhere.** Where you built a tile with OrthoStudio XP (or
imported one from Ortho4XP, or installed another ortho scenery above Live's), Settings, "Your own
tiles", chooses what X-Plane shows:

- "Yours first" (the default): your tile, and Live downloads nothing there: at a start from your
  home airport, X-Plane waits for none of it. The map frames them, and the checks list them. At
  their edges the ground can show a small step: OrthoStudio XP's tiles stand on X-Plane's relief,
  Live's mesh on viewfinderpanoramas'.
- "Live everywhere": Live's photos and mesh there too, with no step. Live lists its pack above
  your tiles in X-Plane's scenery list while it runs; they come back when it stops, and at the
  next start with "Yours first". Taken at the next start, since X-Plane reads its list as it
  starts. The map shows Live's textures on your tiles then, Live stopped too: what its next
  flight shows.

A tile you build, take out or put back in OrthoStudio XP while Live runs counts from the next
flight: once X-Plane is closed, Live sees X-Plane's list of sceneries change and looks for your
tiles again, with their roads and forests, within five seconds.

**Roads, forests and buildings.** Where Live shows its photos, X-Plane no longer draws its own:
X-Plane keeps them in the same file as its ground. Live brings them back as "overlays", X-Plane's
own roads, forests and buildings cut out of X-Plane 12's scenery for every square of its mesh
(18,232 squares; the 1,042 others, the far north, the Caspian Sea and a few islands, have no
X-Plane scenery at all), except where simHeaven X-World brings its own, on your own tiles, and
where another overlay pack is on (AutoOrtho's, Ortho4XP's). Global Forests needs nothing. "No"
leaves the overlays out.

## The mesh, by region

Live lays its photos on OrthoStudio XP's mesh: the ground's shape and the textures' outlines, made
by OrthoStudio XP's engine with the relief of viewfinderpanoramas.org (Jonathan de Ferranti), in
six regions: Europe, North America (Greenland and the Canadian Arctic in), South America, Africa,
Asia, and Australia and the Pacific (Hawaii and Easter Island in). Together they cover all the land
of the world but Antarctica: 19,295 squares.

The panel lists the six, each with what it downloads and the room it takes once installed
(Europe: 16.3 GB to download, as much on the disk), and **Install**. Live downloads it from
OrthoStudio XP's server, a few files at a time, checks each one and unpacks it as soon as it is
whole; the bar shows the download, and the line under it the gigabytes downloaded and the pace in
megabytes a second over the last few seconds. **Stop** stops it, and **Install** again goes on where it
stopped, a file cut off included. A region is used from the next start, never half installed. Its
roads, forests and buildings come with it, unless "Roads, forests and buildings" says "No".
Once installed, a region shows its size on the disk and **Delete** (click twice). When a region
is made again on the server, the panel says an update is available and how much it downloads,
and offers **Update**: only
those are downloaded again. Updating and deleting wait until Live is stopped. A region installed
before the night lights (2.0) gets their grids by itself, a few megabytes (Europe 11.6 MB), as the
window opens: no Update to click. A region may sit on another disk, linked from Live's folder: the
panel says where each one is.

A region AutoOrtho installed on its own is still recognised, and left as it is; Live no longer
offers to install AutoOrtho's. In a terminal, `python -m orthostudio.live regions list`,
`regions install eur` and `regions remove eur` still handle AutoOrtho's regions.

Where no region installed has a mesh, X-Plane shows its own scenery, without photos: Live says so,
in its window and in a small panel in X-Plane ("Live: no mesh here: install North America in the
Live window"), from the map of OrthoStudio XP's regions (`meshmap.json`). Over the
open sea, where no region has any mesh, it says nothing: that is why the shade of the regions on
the map leaves out squares of open sea.

## What happens while you fly

Every two seconds Live reads where the aircraft is, where it is heading and how fast. It looks two
minutes ahead along the track (along your SimBrief route while you follow it), and for every
texture within 300 km it works out how sharp X-Plane will want it when the aircraft passes
closest. The camera does not count: X-Plane takes the textures for the aircraft alone. What is missing is downloaded and built, the most urgent
first. X-Plane picks the new file up by itself the next time it wants that texture sharper.

X-Plane decides how sharp a texture must be from the distance between the aircraft and the
texture's centre, in rings. For a normal texture (about 7 km across), as measured on a 2,704 px
wide image with a 65° field of view (the first measures in flight, 12 % farther since a start
measured on the ground in October):

| distance from the aircraft to the texture's centre | what X-Plane reads | imagery |
|---|---|---|
| 130 to 230 km, when X-Plane loads the area | 256 px | ZL12 |
| 81 km | 512 px | ZL13 |
| 43 km | 1024 px | ZL14 |
| 24 km | 2048 px | ZL15 |
| under 14 km, texture quality at Maximum | 4096 px | ZL16 |

On a wider screen, or zoomed in, X-Plane reads sharp textures farther, and Live builds them farther
too; the Flight card says how far for yours. Airport textures (about 1.8 km across) follow the same
law four times closer. The 4096 px level is never fetched when X-Plane's texture quality is below
Maximum: X-Plane then never reads it.

External views read nothing more: X-Plane takes each texture's levels for the aircraft, when it
loads a flight and then as the aircraft moves, and a camera taken away, even kilometres out near the
ground, shows what it took for the aircraft (measured with X-Plane 12.4.4-b4 on 20 October 2026,
[specs/live/levels-test.md](specs/live/levels-test.md)). Far from the aircraft the ground looks a
little blurry there. A level X-Plane took before Live built it shows the neutral colour, green-grey,
once a camera comes close enough to show it, until X-Plane loads that area again. At a flight's
start, the whole airport and its surroundings (its ZL18 textures, about 7 km across) are built at
4096 px before X-Plane goes on, whatever the runway or the stand: a camera taken anywhere over the
airport finds them sharp.

**The plugin.** To make X-Plane wait at a start, Live puts a small plugin in X-Plane while it runs,
and takes it out when it stops. It holds X-Plane on its loading screen as long as the ortho keeps
arriving, and lets it go when everything there is built, when nothing has downloaded for a minute
(no internet, or a source refusing every request: `LogLive.txt` says why), or when you click Stop.
It never holds X-Plane in flight, and does nothing when Live is not running. A new location chosen
in flight loads no aircraft, so the plugin hears nothing to hold X-Plane at: Live pauses X-Plane
itself, the instant X-Plane writes the new start in its preferences, and lets it go once everything
there is built, or at once if Live stops. On Windows the plugin asks Windows not to grey X-Plane as
« Not Responding » meanwhile. In flight it shows
Live's notices in a small panel (no mesh under the aircraft), and measures X-Plane's frames: Live
logs a frame of 100 ms or more as a stutter, with what it was doing then. While X-Plane flies, Live
builds at most four textures at once, at a low priority: X-Plane's threads always go first.

## Why there are no zoom by flight level, prefetch or waiting settings

AutoOrtho's settings are there to manage X-Plane's wait: since X-Plane is blocked while AutoOrtho
downloads, you choose how much to download, how long X-Plane may wait before it gets a blurry
texture instead, and how far ahead to guess. It is a trade between sharpness and freezes, and you
set it. Live has no wait to manage, and X-Plane itself says how sharp it wants each texture:

| AutoOrtho setting | In OrthoStudio XP Live |
|---|---|
| Minimum zoom | Not needed: the far view is the 256 px level X-Plane reads when it loads an area. |
| Maximum zoom, zoom near airports | One list on Live's tab beside the photo source: 16/18, 16/19 or 17/19. Below 16 is not offered, nor 18 away from airports (16,384 px textures). |
| Zoom by flight level | Not needed: X-Plane already reads less sharp when the ground is farther away, and Live follows it. Only the 8192 px photos stop above about 18,500 ft over the ground. |
| Prefetch radius, look-ahead, interval | Built in and measured: 300 km around, two minutes ahead along the track (along a SimBrief route while you fly it), the rings with an 11 % margin at a start and once the aircraft moves (none while it stands), and nothing for external views (X-Plane takes the textures for the aircraft alone). |
| Maximum wait, time budget, fallbacks | Not needed: X-Plane waits only while a flight loads, until everything it reads there is built. In flight it never waits. |
| Cache sizes, workers, pipelines | Internal. |

What you choose: the photo source, the zoom, and how much disk Live may take.

## Where everything is, and the disk

Live's folder is `~/.orthostudio/live` (`%USERPROFILE%\.orthostudio\live` on Windows), unless its
settings choose another, on the computer's own disk or one of the same kind: APFS on a Mac, NTFS on
Windows, ext4 on Linux, never Mac OS Extended, exFAT or FAT32.

- `pack`: the scenery X-Plane reads, linked into Custom Scenery as `z_osxp_live` while Live runs.
  The files X-Plane looks for are there; the textures are built for real where you have flown, and
  a neutral placeholder that takes no space elsewhere (a disk-usage tool counts every placeholder
  whole: they share one file).
  Its `textures_night` folder holds the night textures, black and sharing one file until
  made.
- `chunks.sqlite`: the downloaded imagery, so that it is not downloaded twice. The photos of the
  sharpest levels (4096 and 8192 px) are kept in the textures themselves instead: a texture made
  coarser for the disk, then built sharp again, downloads them again.
- `regions`: the mesh's regions Live installed, each with the night lights' density grids of its
  squares in its `night` folder.
- `overlays`: the roads, forests and buildings of Live's squares, linked as `y_osxp_live_overlays`
  while Live runs, listed right above Live's pack: X-Plane reads none listed under it.

The textures and the imagery share one disk space, 20 GB by default, set in the Settings tab, and
never under 10 GB at 16/18 nor 20 GB at 16/19 and 17/19: a smaller space is raised to it, and the
window says so (a 20-minute flight took about 4 GB of textures at 16/18 and 12 GB at 17/19). Past
it, Live makes the textures seen longest ago coarser, only where nothing can show it, and drops the
imagery used longest ago. The same tab deletes the textures (built again when flown over) or the
imagery (downloaded again when needed).

On Windows, Live keeps its folder out of Windows Search, whose indexing of its many files kept the
indexer busy for whole flights. Microsoft Defender checks each new file Live writes, which took
about one core for a while at Live's first start. To spare it, add Live's folder as an exclusion
(Windows Security, Virus & threat protection, Manage settings, Exclusions, Add an exclusion,
Folder): a security setting for you to choose, which Live never changes itself.

To switch Live off, put `enabled = false` in `live.toml`, in OrthoStudio XP's own folder
(`~/.orthostudio`, `%USERPROFILE%\.orthostudio` on Windows).

## If something goes wrong

- Live keeps its log in `LogLive.txt`, at the top of your X-Plane folder, beside X-Plane's own
  `Log.txt`: send both when something went wrong. It is written again each time Live's window
  opens, so send it before opening Live again; the ten before are kept in Live's `log` folder.
  The window's Log has a button that shows it in the Finder (File Explorer on Windows).
- Live works with the X-Plane that Build uses: the one named under *Where is X-Plane 12 installed?*,
  in Live's Settings or Build's (it is one setting), or the first one detected when none is named.
  If the window shows another X-Plane than the one you fly, name yours there. Stop Live and quit
  X-Plane first: Live then takes its pack and its plugin out of the X-Plane you leave.
- If Live stops, X-Plane goes on flying over what is on the disk. If the streamer stops on an
  error, the window says so with the error, and the plugin lets X-Plane go on.
- If a texture comes late, X-Plane shows it a little blurry and sharpens it at its next ring.
- If the photo source refuses your connection (access denied), Live says so under the source and
  the textures read "Downloads refused": stop Live, choose another source, start it again.
- Quit X-Plane before Stop: Live takes its scenery and its plugin out when it stops, and waits for
  X-Plane to quit first.
- Quitting the app while Live streams asks first, in the window.

## Limits

- Only where a region is installed: the six regions cover all the land of the planet but
  Antarctica.
- Flown with X-Plane 12 on a Mac and on Windows; not yet on Linux.
- Night lights on Live's photos only: your own tiles ("Yours first") have none yet, and a region
  installed without its density grids lights its towns evenly.
- External views add nothing: X-Plane takes the textures for the aircraft alone, so a camera taken
  far shows the ground there as sharp as the aircraft's distance allows, a little blurry. Live
  notes how far external views take the camera, flight after flight, for information.
- From a checkout of the repository, `uv run python -m orthostudio.live ui` opens the window and
  `stream` runs Live in a terminal; on a Mac the plugin is compiled the first time, which needs
  Xcode's command-line tools (`xcode-select --install`).
