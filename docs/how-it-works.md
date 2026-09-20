# How OrthoStudio XP works

A guide for users: what OrthoStudio XP builds, the steps it goes through, what it keeps on disk and
how to get that space back. For the design and the rules behind each step, see `docs/specs/`; for
the measurements, `docs/benchmarks/`.

## 1. The words

- **Tile**: a 1° × 1° square of the Earth, about 111 km north-south and 77 km east-west at 46° N.
  It is named after its south-west corner: `+46+006` is 46° N, 6° E (Geneva). X-Plane loads
  scenery tile by tile.
- **DSF** (Distributable Scenery Format): X-Plane's scenery file for one tile, a single binary file
  of about 40 MB for an OrthoStudio XP tile at ZL16 (`Earth nav data/+40+000/+46+006.dsf`). It holds
  the relief as a mesh of triangles and, for each triangle, the texture to lay on it. X-Plane ships
  one per tile in its default scenery; the DSF OrthoStudio XP builds replaces it.
- **Texture**: one aerial image of 4096 × 4096 pixels, stored as DDS (compressed for the graphics
  card, with its smaller versions for distance). At ZL16 a texture covers about 7 km on a side,
  so a tile uses about 180 to 230 of them, about 1.4 GB. Each comes with a small `.ter` file
  that tells X-Plane how to lay it.
- **Image piece** (a *chunk* in the code): what the imagery provider actually sends, a
  256 × 256 image. A texture is 16 × 16 = 256 pieces, so a ZL16 tile is about 50,000 pieces,
  0.8 to 0.9 GB to download.
- **Zoom level (ZL)**: the sharpness of the imagery. Each level up halves the size of a pixel on the
  ground and multiplies the number of textures by four: ZL16 is about 2 m per pixel, ZL18 about
  40 cm. OrthoStudio XP builds a tile at one level and sharper *zones* where you draw them
  (airports, places you fly low).
- **Overlay**: roads, buildings, forests, power lines and night lighting. They live in the same
  default DSF as the ground, so a photo tile that replaces the ground would lose them.
  OrthoStudio XP extracts them from X-Plane's own scenery into a shared pack,
  `yOrthoStudio_Overlays`, loaded above the photo tiles. Users of simHeaven X-World, which brings
  its own, answer *None* to the Settings question on roads, forests and buildings; when OrthoStudio
  XP finds such a pack in your Custom Scenery it says so beside the question, and on a first run it
  answers *None* for you: OrthoStudio XP
  then builds no overlay, and a tile built again takes its old one out of X-Plane. A
  `yOrthoStudio_Overlays` line you disable in X-Plane stays disabled. One thing is left behind on
  purpose: the line X-Plane 12 draws around airport grass to blend it into the terrain. Over a
  photograph it has nothing to blend and shows as an outline around every airfield, so it is
  dropped.

## 2. The steps

A build shows six steps per tile on the Works screen. A tile's Data step starts by downloading its
OpenStreetMap data when it does not have it yet: about 8 to 15 s a tile when the public Overpass
servers answer (measured 2026-09-14), with the layers received and the download rate shown in the
step. One tile downloads at a time, since the servers refuse more, but the other tiles do not wait:
each goes on with its relief at once, and with the rest as soon as its own data is in. A server
that does not answer is left aside for every tile, instead of being waited for by each.

| Step | What it does | Typical time (ZL16, one tile, M4 Pro) |
|---|---|---|
| **Data** | Downloads, then reads the OpenStreetMap airports, roads, coastline and water, and the elevation of X-Plane 12's own scenery. Produces the lines the relief must follow: shores, flat runways, rivers. | 5-15 s after the download |
| **Terrain** | Cuts the relief into triangles, finer where it matters: mountains, shores, airports. | 3-12 s |
| **Coast** | Builds the water masks: soft transitions between the photo and X-Plane's water. | 1-4 s |
| **Imagery** | Downloads the image pieces, assembles them into textures, compresses them to DDS. | 40 s to 2 min if nothing is cached (the provider's speed decides), 10-15 s if the pieces are |
| **Assembly** | Writes the DSF, extracts the overlay, gathers everything in the tile's folder. | 5-15 s |
| **Install** | Links the tile into X-Plane's Custom Scenery (on Windows, a junction when the account may not make links) and writes its line in `scenery_packs.ini`. | under 1 s |

Several tiles are built at once: the relief of up to three tiles is computed at the same time,
the masks and DSFs of the others alongside, and the image encoding uses all the cores but two.
Downloads go through one network lane: one tile's imagery after the other, with up to 128
requests to the provider at the same time (a limit measured not to get throttled,
`docs/benchmarks/network.md`). On a cold build the imagery is most of the time, and the
processor mostly waits for the provider.

**An image piece that does not arrive.** Out of tens of thousands of requests, a few can stall while
the others flow. Once the rest of the tile's imagery is done, OrthoStudio XP asks for them again
after a pause of 5 s, then 15 s, then 45 s, each time through another Bing server, and for at most
3 minutes per tile. Only a piece still missing after that leaves its texture incomplete: the tile is
then not installed, and *Retry the missing ones* fetches just those pieces. The final report counts
the pieces asked for again and those recovered.

**Progress and time left.** The percentage and the time left on the Works screen cover the whole
build, the OpenStreetMap downloads included. Each step weighs what it usually costs, the imagery
most of all. The download time starts from the speed your connection showed on your last builds
(read from their reports, the latest ones counting most), and the estimate corrects itself with the
speed the build actually shows, moving smoothly rather than jumping. A step reads *running* only
while it works, and *waiting* when part of it is done and the rest waits for its turn.

**Several builds.** One build runs at a time. A build started while another runs waits in a queue
and starts as soon as the one before it ends; the Plan stays open to choose and queue more. A tile in
a build, under way or waiting, cannot be chosen again until that build ends, and its Library buttons
wait too: the end of the build decides what X-Plane shows of it. Tiles are deleted only between
builds. *Remove from the queue*, on the Works screen, cancels a build that has not started, and
*Quit* cancels the waiting builds with the one running.

**Imagery sources.** Bing Maps and Esri cover the whole world and come first in every list. The
other sources are a country's (the Netherlands, Spain, Luxembourg, Japan, the United States): the
list puts first those that cover the tiles you chose, and a source that misses a tile says so,
since its server has no image there. *My sources…* adds a source OrthoStudio XP does not ship,
from the address of one of its tiles, tried on one tile first: you add it yourself, and its terms
of use apply to you. It is kept in `sources.toml` in OrthoStudio XP's folder.

**Settings.** The Settings screen asks what you want to see in X-Plane rather than naming technical
parameters: how much detail, sharper airports or not, how the coast fades into the sea, which water,
how much photo on lakes, where the relief comes from (X-Plane 12's own, the Copernicus relief at
1 arc-second, the USGS 3DEP at 1/3 arc-second over the United States, Canada's lidar or the ANADEM
terrain of South America over Copernicus, or your own file), and whether OrthoStudio XP adds
X-Plane's
roads, forests and buildings. Three presets answer several questions at once; every other setting is
under *For experts*, with its name in Ortho4XP. The Plan recalls the answers in one sentence before
you build, and works out the cost by itself as you choose: disk space, download and time, with
nothing to press first. *Build* waits only when the disk cannot hold the build, and says so.

**Where X-Plane is.** OrthoStudio XP works with the X-Plane 12 installed on the same computer: it
takes the relief, roads, forests and buildings from X-Plane's own scenery, and adds the tiles to its
Custom Scenery. It finds X-Plane by itself, first in the list X-Plane's installer keeps, then in the
usual folders (`~/X-Plane 12` or `/Applications/X-Plane 12` on a Mac; `C:\X-Plane 12`, or
`X-Plane 12` on the desktop or in your user folder, on Windows). With more than one X-Plane 12 on
the computer it takes the first, names the others in Settings, and the Library says which one its
*In X-Plane* column speaks of: a user installed a tile into an X-Plane 12 he had forgotten, and
found nothing in the Custom Scenery of the one he flies. When it finds none, for example in
a virtual machine whose X-Plane is installed on the host, step 3 of the Plan says so before any
estimate, and its button opens the Settings question *Where is X-Plane 12 installed?*, where
*Choose the X-Plane folder…* opens the Finder's (or the File Explorer's) folder window: nothing is estimated
or built until the folder is known.

**Starting and stopping.** OrthoStudio XP is a program that runs on your computer; nothing leaves
your computer except the downloads of imagery and map data. Open the OrthoStudio XP app to start it:
a window opens saying "Opening OrthoStudio XP…", then shows the page as soon as it is ready. Opened
again while it runs, the app brings its window back rather than opening a second one. *Quit*, at the
top right of the page, stops it, after asking when a build is running. Closing the window does not
stop OrthoStudio XP: the window goes out of sight, its icon stays in the Dock, a build goes on, and
a click on the icon brings the window back where you left it, as does *OrthoStudio XP* in the
*Window* menu, or Cmd+0. **Nothing closes the app but you**:
*Quit* in the page, or *Quit OrthoStudio XP* in its menu, in the Dock's menu or with Cmd+Q, which
all bring the window back in front and ask the same question, telling you when a build is running. Opened in a browser instead of a window,
where there is no icon to show that it runs, it still stops by itself five minutes after its last
page closed, with no build running or waiting.

The window is drawn by the web view your system already carries, so the page is the same one a
browser would show. On Linux, where that web view is a package your distribution installs and
OrthoStudio XP will not install anything for you, the page opens in your browser instead; the
`window` line of *Checks*, at the foot of the page, says so and names the package. What the
engine writes as it works goes to `serve.log`: `~/Library/Logs/OrthoStudio XP` on macOS,
`%LOCALAPPDATA%\OrthoStudio XP\Logs` on Windows, `~/.local/state/OrthoStudio XP/log` on Linux. The
app carries its own Python and its two helper programs, Triangle4XP and DSFTool; removing the app
leaves your tiles, cache and settings in `~/.orthostudio` (and in the data folder, when you chose
one).

## 3. What is on the disk

OrthoStudio XP keeps your settings and its lists in `~/.orthostudio` (or `$OSXP_HOME`), and what
takes space in its **data folder**: `~/.orthostudio` too, unless Settings name another folder under
*Where should the tiles and the downloaded imagery go?*, on an external disk for instance.

```
~/.orthostudio/
  config.toml      settings;  zones.json  the zones drawn on the map;  sources.toml  your sources
  library.sqlite   the tiles OrthoStudio XP knows (the Library screen)
  jobs/            build journals
  airports.sqlite  X-Plane's airports, for the search of the Plan

the data folder (~/.orthostudio unless Settings name another one)/
  chunks/          image pieces downloaded (raw material)          ≈ 0.9 GB per ZL16 tile
  store/           the cache: the result of every step            ≈ 1.5 GB per ZL16 tile
    orthostudio.osm/  orthostudio.dem/  orthostudio.vectors/       (small)
    orthostudio.mesh/  orthostudio.masks/  ...
    texture.dds/                 the finished textures (nearly all the space)
    tile.dsf/  tile.overlay/  tile.textures/  tile.pack/ ...
    index.sqlite                 what is in the store, and what each result was made from
  tiles/           the tiles ready for X-Plane                     ≈ no extra space
    zOrthoStudio_+46+006/
      Earth nav data/+40+000/+46+006.dsf
      terrain/*.ter   textures/*.dds   orthostudio.toml   tile_settings.cfg
    yOrthoStudio_Overlays/Earth nav data/+40+000/+46+006.dsf
  work/            build reports and temporary files
  osm/             the OpenStreetMap data downloaded
  mapcache/        base-map images of the page
  elevation/  dem/ relief files downloaded when the relief is not X-Plane's, and the ones found missing

X-Plane 12/Custom Scenery/
  zOrthoStudio_+46+006    →  link to <data folder>/tiles/zOrthoStudio_+46+006
  yOrthoStudio_Overlays   →  link to <data folder>/tiles/yOrthoStudio_Overlays
  scenery_packs.ini          the order X-Plane loads scenery in
```

Nothing takes its space twice. The files in a tile's folder are the same files as in the store
(hard links, not copies), and X-Plane sees a link to the tile's folder, not a copy of it.

**Another data folder.** Choose it in Settings, with *Choose the folder for the tiles…*; its disk must be able
to link one file into two places, as the store and the tiles share their textures: APFS or Mac OS
Extended on a Mac, NTFS on Windows, ext4 on Linux. exFAT and FAT32, the format many external disks
come in, cannot, and each tile would take three times its space, so such a folder is refused.
Nothing is moved: the tiles built before stay where they are and keep working in X-Plane, and the
next ones go to the new folder. Each folder has its own `yOrthoStudio_Overlays`, which Custom
Scenery lists as `yOrthoStudio_Overlays_2` (then `_3`...) when X-Plane shows the tiles of two
folders at once, so every tile keeps its roads, forests and buildings. A tile built again into the
new folder takes the place of its old build in X-Plane, and the old build stays in the Library,
where it can be deleted. When the disk is not plugged in, the status bar says so, the page's map
still shows without keeping its images, and nothing is estimated or built: nothing is written on
the computer's own disk instead.

`tile_settings.cfg` lists the settings the tile was built with. The tiles Ortho4XP built keep their
own folders, `zOrtho4XP_<tile>` and `yOrtho4XP_Overlays`, which OrthoStudio XP never writes into.

**The colours of the photos.** *Are the photo colours right for you?* takes aerial imagery as the
source delivers it, tones it down a little, a lot, or by your own numbers (brightness, contrast and
colour, under *For experts*). It is applied when the textures are encoded, which is the last step
that reads the downloaded images: changing your mind builds the tile again **without downloading
anything**, in a minute or so. A user of the X-Plane.Org page asked for it after editing his
screenshots by hand (2026-09-18).

**Colours square by square, and zone by zone.** Settings answers for everything you build; step 1
of the Plan sets the colours of the squares you chose (with the same sliders and the same preview);
and a zone drawn inside a square can differ again in its own polygon. Each level inherits the one
above until it names its own, and a texture belongs to the zone its centre falls in. Changing your
mind re-encodes the textures concerned and downloads nothing. **The map shows it**: a square or a
zone with its own colours is repainted where it is, so you judge the result on the ground you fly
over rather than on a thumbnail. Two buttons undo a choice: *Give the colours back to Settings*
clears everything, and *Go back to the colours already built* gives each square the colours of the
tile you already have on the disk -- the Library says which tiles no longer match, and this is how
you agree with them again without building anything.

**A street map, if you want one.** The legend has a box that swaps the aerial photo for
OpenStreetMap, to read towns, roads and names before choosing a square. The map comes from
OpenFreeMap, a free service that renders OpenStreetMap without a key; OrthoStudio XP serves it
through its own engine, so nothing on the page talks to another site, and what is downloaded is
kept in the map cache. The renderer it needs is loaded the first time you ask for the street map,
not before.

**Airports on the map.** The legend has a box for them: OrthoStudio XP knows about 39 000
aerodromes and draws those of the view, with their ICAO code, over the photo. Only the fields that
have a real code are drawn; the rest carry an identifier of X-Plane's own, which says nothing to a
pilot. It costs no download, the
list travels with the app, and it answers the question the aerial imagery often does not: is the
field I want inside this square? They appear from zoom 8 in, where there are few enough to read.

**A zone alone builds nothing.** A build makes whole tiles -- that is how X-Plane cuts its scenery
-- so a zone drawn where no tile is chosen simply waits: it is saved with the others, its row says
*outside the selected tiles*, and *Add N tile(s)* beside it chooses the tiles it falls in. Nothing
is chosen for you: a zone can cover several tiles, and each tile is a download.

**Your own elevation files.** Many countries publish lidar surveys far finer than the worldwide
data, and enthusiasts republish them square by square (Sonny's models of Europe, for instance).
Settings' *Do you have elevation files of your own?* takes the folder they are in: each tile takes
the file of its own square from it, at any resolution, subfolders included. The files must be named
after their square, as the SRTM data are: `N47E011.hgt`, or `.tif`. If you keep several sets of the
same place, 3", 1" and 0.5" for instance, the finest file is used for each square, and a file finer
than the relief under it raises the whole tile rather than being read at the coarser step: what you
downloaded is what is built. The finest wins the other way round too: where the relief you chose is
sharper than your file, as the United States relief at a third of an arc-second is against a file
of one second, the relief answers and the report names the file it left aside. Where your folder has nothing, the relief you chose stays in charge, so
a collection that covers one country is no trouble. Change a file and the tiles that use it are built again; the rest are not. A single file
works too, under *My own elevation file*, and writing `{latlon}` in its path where the name of the
square goes (`/my-relief/{latlon}.hgt`) makes it one file per tile without naming a folder.

**Hand-made mesh patches.** Under *For experts*, *Folder of hand-made mesh patches* takes a folder
of yours, with one directory per tile: `+46+006/my-relief.patch.osm`, the files JOSM writes. A patch
published for Ortho4XP fits as it is, with the tree it comes in
(`Patches/-20-050/-20-044/SBCF.patch.osm`): name that folder, or its `Patches`, or the tile's own
directory, whichever you have. *Choose…* opens the folder dialog; left empty, OrthoStudio
XP reads its own `patches` folder, `~/.orthostudio/patches`, which it makes empty when it starts
so that there is somewhere to drop them. A tile that has a
directory there is built with its patches, and editing a patch builds that tile again by itself; a
tile without one is built as before.

**With AutoOrtho, XPME or Ortho4XP tiles.** For each square X-Plane shows the ground of one pack
only: the OrthoStudio XP tile, listed above AutoOrtho, XPME and the other base meshes. But it draws
the roads, forests and buildings of every active pack that has the square, and AutoOrtho's, XPME's
and Ortho4XP's overlay packs have theirs: on such a square they come twice. The Library says so,
and one click leaves those roads to the other pack for these squares: OrthoStudio XP puts its own
aside in the tile's folder, where X-Plane does not read them, and touches nothing of the other
tool's. If that pack is switched off later, the Library says the square has no roads any more, and
one click takes OrthoStudio XP's back. The choice stays when the tile is removed from X-Plane, added
back or built again.

OrthoStudio XP does not need Ortho4XP: it never runs it and a build never reads an Ortho4XP folder.
It downloads its own OpenStreetMap data and elevation files. The only time it looks into an Ortho4XP
folder is when you import the tiles Ortho4XP built there, from the Library (*Import my Ortho4XP
tiles*) or with `osxp import-ortho4xp <folder>`: they are listed beside yours, can be installed
and compared, and nothing is written into that folder.

In `scenery_packs.ini`, OrthoStudio XP puts the overlays just above the photo tiles, both below
custom airports and object packs (such as simHeaven) and above AutoOrtho and base meshes. Before its
first change it keeps the original as `scenery_packs.ini.bak`, and before every change the previous
version as `scenery_packs.ini.osxp-previous`.

## 4. Why a cache

Every result in the store is filed under a fingerprint of everything that produced it: the
settings, the input data, the version of the rule. A step whose fingerprint is already in the
store is not run again.

- Building an unchanged tile again takes a few seconds: every step is "already done".
- Changing one thing, a sharper zone around an airport for instance, runs again only the steps
  that depend on it: the textures of that zone and the assembly, not the relief.
- Image pieces already downloaded are never downloaded again, even for a different build.

Most of the time OrthoStudio XP saves in real use comes from here, where tiles are built, adjusted
and built again.

## 5. What stays after each action

| Action | Tile folder and X-Plane link | Cache (`store`) | Image pieces (`chunks`) | Relief (`elevation`) |
|---|---|---|---|---|
| Build | added | results added | pieces added | cells added |
| Remove from X-Plane (Library) | the link goes, the folder stays | stays | stays | stays |
| Add to X-Plane (Library) | the link comes back, at once | stays | stays | stays |
| Delete (Library, `osxp uninstall --delete`) | everything goes | what only this tile needed, unless used in the last 10 minutes | stays | stays |
| `osxp clean` | - | what no tile on disk needs, unless used in the last hour | stays | stays |
| `osxp clean --images` | - | same as `osxp clean` | emptied, with the map cache | stays |
| `osxp clean --relief` | - | same as `osxp clean` | stays | emptied |
| `osxp clean --all` | - | everything no tile on disk needs, however recent | emptied, with the map cache | emptied |
| Free space (Library) | - | everything no tile on disk needs, however recent | emptied with the map cache if you tick the box | emptied if you tick its own box |
| Clear the job list (Works) | stays | stays | stays; only the finished jobs' progress and logs (`jobs/`) go | stays |

The delays protect a build that may be running at the same time, in the page or in a terminal: it
could still need a result it used a moment ago. *Free space* and `osxp clean --all` have no delay,
so they refuse to run while a build is in progress.

Deleting a tile keeps its image pieces and its elevation cells on purpose: building the same area
again then skips the download, which is most of the time. The relief has its own box because it
weighs much less and comes back much faster: 40 MB a square from Copernicus, against 800 MB of
imagery at ZL16. To give everything back, use *Free space…* at the bottom of
the Library (it shows the sizes and asks first), or from a terminal:

```bash
osxp clean --all --dry-run   # says what would be freed, deletes nothing
osxp clean --all
```

Only the tiles still on disk are kept, with what they need. A tile built after a full clean
downloads its image pieces again.
