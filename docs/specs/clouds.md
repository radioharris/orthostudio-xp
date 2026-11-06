# Replaced clouds

Status: written with the code, 2026-11-06. A user asked whether the clouds of a tile's photo could
be found and replaced by the same ground from another source, its colours blended into the tile
(2026-11-03). One rule decides every choice here, the owner's: what the cloud mode shows on the
tile is what X-Plane shows. The measurements are in `docs/benchmarks/clouds.md`, cited by their
number (B1, B2...).

## 1. What the user sees

### 1.1 Words

- **Cloud / nuage**: a place on the photo where the ground does not show: thick or thin cloud,
  with its shadow when it falls beside it. The app finds them; the user outlines one it missed.
  Each keeps its name for as long as it exists, "Cloud 7" found, "Outline 2" drawn.
- **Its tick and its source**: ticked, the place shows the ground of the source named in its row;
  unticked, the tile as photographed. Choosing a source ticks; unticking keeps the source.
- **Getting its images / récupérer ses images**: what a tick does: the source is read at the
  tile's sharpness, judged, frozen and previewed (4.3). Only a cloud whose images are in is
  applied.
- **Apply / Appliquer**: X-Plane changes only then (or with **Restore the original photos**).
  Every other change waits in the tile's list, kept by the engine.
- **Before / Avant, After / Après**: Before is the tile with no cloud replaced; After what X-Plane
  shows, or will show once the waiting changes are applied.
- **Replaced / remplacé** (held, in the code): a cloud the tile's textures show laid in them.
- A cloud a build did not lay again, the texture under it having changed (R6), is on the page a
  ticked cloud waiting for its images to be got again, its line saying why.

### 1.2 The Library

A column **Clouds**: the button **Clouds…**, which opens the cloud mode on the tile, and in a few
words what the tile holds and what waits: "3 replaced", "2 to apply". The engine says which rows
have it (`GET /api/clouds`): a tile OrthoStudio XP built, on the row of its own folder, there. A
tile with no list yet shows the button alone, and no other tile's folder is read for it.

### 1.3 The cloud mode

The Plan's map, brought over the tile; the Plan's steps make way for the cloud panel.

- **On the map**: the tile drawn as X-Plane draws it (R2). In **After**, each ticked cloud's
  preview over it, in the list's order; in **Before**, none. Every cloud's outline: yellow when
  unticked, the accent when ticked, the current one thicker. A click on an outline ticks or
  unticks it, except while a cloud is being outlined.
- **The banner over the map**: **After**, **Before**, **Outline a cloud**, the sentence of the
  view ("What X-Plane shows.", "What X-Plane will show after Apply.", "The tile with no cloud
  replaced."), **Back to the Plan**.
- **The panel**: the tile's name; the tile's line when it is not ready (1.5); each task under way
  with its phase, its progress and **Stop**; the list, a row per cloud; the count of what Apply
  changes ("To replace: 1 · Back as photographed: 2"); **Find clouds**, **Apply (n changes)**,
  **Discard changes**, and **Restore the original photos** when the tile holds replaced clouds.
- **A row**, laid out as the Plan's zones: its tick and name, its area, **Show** (the map over
  it), a trash for an outline the tile does not hold; the menu of its sources, each with what it
  has there and its fit ("Esri World Imagery · clear · fit 93 %"), both from its newest reading
  (`state.reading`: its images got, else the search's), the best first, those that cannot be
  chosen greyed (`sources.PROPOSABLE`: the same photo, no picture; the source whose images the
  tile shows of it always may be); its line (1.5); **Get its images** when asking again may end
  its wait; **Stop** while a task of it runs.

### 1.4 What the user does

| Action | What happens |
|---|---|
| **Find clouds** | The search (4.1), one at a time for the app; the clouds merged into the list (R3) |
| Tick, a click on an outline | The cloud ticked with its source (the best when none was chosen); its images got when its state asks them (`check`, 1.5). A click on an outline does what its state says (`toggle`) |
| Untick | The cloud unticked, its source kept; nothing runs |
| Choose a source | Ticks it with that source; its images got |
| **Get its images** | Asks the source again (after no answer, a refusal, or a photo that changed) |
| **Outline a cloud** | Drawn as a zone is, with the Plan's own drawing (`map.js` `drawShape`): points clicked on the map, inside another cloud's outline too, a double-click or Enter ends it, Escape gives up. The found clouds it covers that are neither ticked nor held leave: it is them. Its sources are tried; once that ends, it is ticked with the best and its images got, as its state allows |
| Trash of an outline | Removes it from the list; an outline the tile holds has none: unticked, it comes back at Apply, and its trash comes then |
| **Apply** | Asks first, saying what changes and what waits; lays the list's ticked clouds in the tile's textures, in a few seconds (R4, 2.3) |
| **Discard changes** | The list back to what the tile holds |
| **Restore the original photos** | Asks first; every cloud unticked and every texture back as built |
| **Stop** | Ends the task at its next step, the list as it was (Apply has none: it ends in seconds) |

### 1.5 The states

Every rule of the mode is in one place (`clouds/state.py`): from two facts, the tile's list (2.4)
and what its folder shows (2.1), the state of the tile and of each of its clouds; and from those
states alone, everything the page shows and every action the engine takes. The page decides
nothing: it shows the engine's view (`state.View`), a click on an outline included. The routes
refuse what the view does not allow (`CLOUD_TILE_NOT_READY` for the tile, `CLOUD_INVALID` for a
cloud), and Apply lays what the view says it lays (`View.laid`), computed by the very function
Apply runs (`laying.plan_folder`).

The tile's state, the first that holds (`state.TILE_STATES`, `tile_state`):

| State | When | The tile's line |
|---|---|---|
| `data_away` | the data folder's disk is not connected | the map, finding and Apply come back with it |
| `disk_away` | the tile folder's disk is not connected | |
| `in_build` | the tile is being built | its clouds are here again once the build is over |
| `queued` | it waits in the queue | the same |
| `not_built` | no folder of ours for the tile | |
| `not_whole` | its folder is not the pack its manifest names (`pack_is_intact`: a build cut short, files taken from it) | build it again |
| `ready` | | |

**Ready** allows everything, any other state nothing. In a build or in the queue the tile's
folder is not read at all (its state is told from the build's queue first, `clouds_api.look`):
the list is shown as it is, read only, and nothing is counted. No Apply or Restore while a task
of the tile runs, nothing but the list's edits while Apply or Restore does (`state.possible`); of
what remains, only what would do something is offered (`allowed`): Apply a change, Restore a
replaced cloud, Discard a change or a wait, Find while no search runs. Any tile of ours can have
its clouds replaced, whatever version built it. The actions have one set of names, the same for
the tile, its rows, the routes, its tasks and the page: `tick`, `untick`, `choose`, `check` (get
a cloud's images), `remove`, `discard`, `find`, `outline`, `apply`, `restore`, `stop`; a task is
named by the action that starts it.

A cloud is, one way only:

- its **source** (`state.source_of`): the one the user chose while it may be chosen there, else
  the best clear one, else the one whose images the tile shows of it;
- its **images**: the newest got of that source when they can be laid (`tile.lays_not`, R6), else
  those the tile shows of that source;
- what **Apply lays** for it: ticked, its images, else what the tile shows of it (a cloud waiting
  keeps its ground); unticked, nothing; then what the folder can lay (`plan_folder`: a cloud the
  tile shows needs its frozen parts only where its texture is made again);
- its **row**, what Apply does with it: read only; a task of it (getting its images, trying an
  outline's sources); **held** (the tile shows it as Apply lays it); **another** ("EOX now, Esri
  after Apply"); **ready** (replaced at Apply); **back** (replaced, Apply lays nothing: it comes
  back as photographed); **waiting** (ticked, nothing new laid: it keeps what the tile shows of
  it); **found** (unticked). Why it waits is said on any row that has a reason, in the warning
  tone: no source, not got yet, what its source answered (5.1), the texture under it changed
  since (its level, source, layout or photo), its images no longer on this computer;
- its **actions**: choose a source the menu offers; tick (with a source) or untick; **Get its
  images** (`check`) when asking again may end its wait; remove an outline the tile does not
  show; Stop while a task of it runs.

The count is the rows': to replace, another source, back as photographed, waiting (`counts`);
Apply's button and the questions of Apply and Restore are said from it (`texts`). After shows
what Apply lays, each by its preview. A cloud's images are asked for one way
(`clouds_api.get_images`): when its state offers `check`, after a tick, a source chosen, an
outline's sources tried, or when **Get its images** asks again. What runs the cloud detector is
asked just before its task starts (a search, an outline, getting images), refused while the
detector is not on this computer and the user has not said to download it
(`CLOUD_DETECTOR_ASK`, with its size): the page asks, and on yes sends the same action again with
`download`; a tick or a source is kept either way. The answer says when something is under way
(`busy`: a task of the tile, or a build of it), for the page to read it again, and every word
the page shows of the tile, a key and its parameters: the tile's line (its state's, else that a
search found none or none was made), each row's line with its tone, the count, Apply's button and
the questions of Apply and Restore. The test of the mode walks every combination of tick, chosen
source, what the tile shows, the images got and a neighbour in the same texture, and checks that
the rows, the count, the buttons, Apply and Discard agree (`tests/test_clouds_state.py`).

A cloud a build did not lay again is a ticked cloud whose images no longer stand (R6): its line
says why, "The tile's level changed since its images were got: get them again", and nothing else
says it.

## 2. What is kept

The clouds are no part of the build: no step, key, setting or lock of it knows them. They change
the finished tile's folder alone, after the build, and a build of the tile lays them again once
it has placed its textures (2.3), the one place the build calls them.

### 2.1 What the tile shows

A texture showing clouds is the texture the build placed, the blocks a cloud reaches made again
(4.4), every other block the build's, byte for byte, at every level: the same size, the same
header but for its stamp. The stamp is in the 44 bytes DDS leaves to tools (`dwReserved1`, which
X-Plane does not read; nvtt writes its own tags there): `OSXC`, the digest of the texture as the
build made it, and the digest of the clouds laid in it (`tile.Stamp`, `clouds_digest`). The
texture as the build made it is kept beside, the same file (a hard link where the disk allows
one, else a copy), in the tile folder's `clouds/<name>.dds`. A texture without a stamp is the
build's own and shows no cloud.

So what the tile shows is read from its folder alone (`tile.read_tile`): a texture says itself
whether it shows clouds, and which; a write cut short leaves each texture whole, the old or the
new, and saying which; laying always starts from the build's texture, never over a laid one. Each
texture's stamp is read with the identity of the very file it is in (inode, size, time), and a
texture that cannot be read is said (`CLOUD_TEXTURE_UNREADABLE`), never taken for the build's.
Beside
the textures, `clouds/<digest>.json` lists the clouds of each digest a stamp names
(`tile.sidecar_path`): the tile shows the clouds every texture they reach names, the same cloud
in each (`TileClouds.held`). Nothing else, not the list, says what the tile shows. The build's own checks are those of any tile
(`pack_is_intact` counts the textures and the DSF, which keep their names and sizes).

### 2.2 A cloud entry

```json
{"id": "z9lo5c8m", "source": "Arc", "match": [[1.03, 1.03, 1.18], [57.1, 52.8, 47.5]],
 "rings": [[lat0, lon0, lat1, lon1, ...]],
 "parts": {"5008_2224_BI14": [15, "<part digest>"]},
 "plain": {"5008_2224_BI14": "<digest of that texture as built>"}}
```

`match` is a gain and a shift per channel, the source's colours brought to the photo around the
cloud; `parts` per texture it reaches, the level the source is read at and the frozen part (2.5);
`plain` per texture, the digest of the texture as the build made it that its images were got on
(R6). What lays it is these fields (`tile.LAID_KEYS`); the list adds its kind and number.

### 2.3 Apply, Restore and a build after

What X-Plane shows of a tile's clouds changes one way (`laying.show`), under the tile's lock (one
at a time in the program, and no picture drawn meanwhile): the folder made to show those of the
clouds given that can be laid (`laying.lay_folder`, `plan_folder`), then the list told which it
shows (`laid`, what a build lays again), when that changed. Each texture they reach is laid from the build's own; each other texture showing
clouds gets the build's own back; a texture showing these already is left as it is; the build's
textures kept aside for textures that show no cloud now go, and `clouds/` with them when empty.

All or nothing: every new texture is made first, beside its own (`<name>.dds.osxp-clouds-new-
<pid>`); then each is put in place in turn, only over the very file read (its identity, 2.1: a
build that placed its own meanwhile, in the app or on the command line, refuses Apply,
`CLOUD_TILE_NOT_READY`, and its texture stays), the one before kept beside until all are in
place. When one cannot be (X-Plane holding it, on Windows: `XP_PACK_CONFLICT`), those already put
in place are put back: nothing has changed, the list neither. Files a program cut short left
beside the textures go at a later laying, once the program that made them is gone. **Apply** gives it the clouds of R4,
**Restore** none (every cloud unticked). Apply is a task of the engine (R7), a few seconds, which
Stop does not cut: it ends what it began, and Quit waits for it.

At the end of a build of the tile, once its pack is in its folder, the build calls the clouds
once (`build._clouds_again`, `laying.lay_again`): when clouds were laid and the folder is the
tile's own (`tile.own_folder`, not a command line's other output folder), the clouds last laid
are laid again, those whose images stand on the textures the build placed (R6). A build of the
same settings placed the same textures: the same clouds come back, the same bytes. Nothing of the
clouds fails a build: an error there is written in the log and the build goes on.

### 2.4 The list

`$OSXP_HOME/clouds/<tile>.json`, format `osxp-cloud-list-3`: every cloud with its id (eight
characters, never given again on that tile), its number, its kind (`found`, `drawn`), its
outline, its area, what each source has under it (`screening`, the search's), its tick, the
source the user chose (`chosen`, none for the best), and per source tried its check (its answer,
fit and entry); and the clouds last laid in the tile (`laid`, entries in laying order). It keeps
what the user did; what each cloud is follows from it (1.5). Only `clouds/cloudlist.py` writes it
(`pick`, `merge_search`, `add_outline`, `remove_outline`, `record_screening`, `record_check`,
`discard`, `untick_all`), and `laying.show` its `laid`. A cloud's area is its outline's. The list is the draft: each change is written
as it is made, one writer at a time per tile, and survives closing, quitting and every build. Its
order is the laying order. Reading it changes nothing: a file that is not a list of this format
is read as no list, and the next write puts it aside first (`.broken-<n>`, each kept); a passing
read error is raised, never a draft dropped for it. Deleting the tile, in the app or with
`osxp uninstall --delete`, deletes its list and its previews when the folder deleted is the one
its clouds are laid in (`cloudlist.forgetting`).

### 2.5 Frozen parts

A part is the source's pieces a cloud reads in one texture (`OSXPPRT2`, `clouds/layer.py`),
written when its images are got, in `<data>/clouds/parts/<digest>.part`. Laying never asks a
source: it lays parts. A part found nowhere when a cloud is laid is said (`CLOUD_TEXTURE_LOST`).

### 2.6 Caches

`<data>/clouds/<tile>/`: the tile's picture (a JPEG named by its build and level) and the
previews (PNG named by their digest). Every path of the cloud files is in `clouds/tile.py`. The
sources' pieces are kept as a build's parent tiles (`ParentCache`, per source). The detector's
classes of the tile's last search are kept in memory for an outline. Every one of them can be
deleted: it is made again.

## 3. The rules

### R1. The tile keeps its clouds

Every build of the tile lays its clouds again where they stand (2.3), whatever starts it: the
Plan, the Library, Works, the command line. A build knows nothing else of them: its keys, its
cache, its settings and its jobs are those of a tile without clouds.

### R2. The map is the tile as X-Plane draws it

The picture is drawn from the tile folder's DSF, triangle by triangle, each with the texture its
terrain names, read at the level of its DDS that matches (`clouds/picture.py`). Land shows the
photo whole; the sea shows it at the texture's alpha (the sea mask) over X-Plane's water; inland
water at that alpha times the lakes' share (`ratio_water`, the DSF's eighth plane, through the
water transition ramp) over its water; X-Plane's water alone, no photo. Each texture is read as
the build made it (`TileClouds.plains`), so Before is the tile with no cloud replaced.

### R3. A cloud is one place

A cloud keeps its id and its number for ever; neither is given again on the tile. A search merges
into the list: a found cloud over one that is held or ticked is that one (it keeps its tick, its
source, its outline); found clouds over each other are one; an unticked found cloud the new search
no longer finds leaves. An outline takes the place of the unticked found clouds it covers, half of
one or more inside it (`cloudlist._covers`). A cloud leaves the list only when the tile does not
hold it (`remove`, 1.5).

### R4. The list is a draft; Apply lays what its question showed

What Apply would do is said once, by each cloud's state (1.5, `state.view`, which plans the folder
with `laying.plan_folder`, the function Apply runs), for the count, the rows, After and Apply.
Apply lays each ticked cloud with its images when they can be laid, else what the tile shows of
it (a replaced cloud given another source keeps its ground until the new one's images are in); a
ticked cloud with neither waits, left out, and the question says so. After shows exactly these,
each by its preview (R5): what Apply would lay is what the map shows. The count is the rows':
replaced (laid, not held), another source (laid with another than held), back as photographed
(held, not laid), waiting (ticked, nothing new to lay). After Apply nothing is left to change;
after Discard nothing ticked differs from what the tile shows. Only the textures under the clouds change, and in them only
the blocks a cloud reaches.

### R5. What you see is what is laid

One function makes a cloud's layer on a texture (`clouds/layer.py`, `layer`), and one gives its
colours, matched to the photo and clipped to what a pixel holds (`Layer.rgb`); Apply lays them,
and the preview is the same layer in the same colours reduced to the map's level, weighted by its
alpha in linear light as the texture's levels are made (`find._lay_reduced`), laid where X-Plane
shows that texture, over water at the share X-Plane gives the photo. Measured: B5.

### R6. A cloud stands on the texture it was got on

`tile.lays_not` is the one rule, for Apply, a build after and the mode alike: a cloud's images
can be laid while they stand (`tile.stands_not`: the textures they reach are those they were got
in, the same level, source and layout, and each of them, as the build made it, is the one they
were got on, `plain`, its digest) and their frozen parts are on this computer (a data folder
changed or emptied takes them away). A cloud the tile shows needs its parts only where a texture
of it is made again. Another
build with other colours, another photo, another level there makes another texture: the cloud is
not laid, whole, on any texture; the others are. In the list it stays ticked and waits, its line
saying why, for its images to be got again; one Apply then puts it back. A held cloud that no
longer stands waits likewise: Apply, or the next build, takes it out.

### R7. The engine's tasks

A search, getting a cloud's images, trying an outline's sources and Apply are the engine's tasks
(`clouds/tasks.py`): each runs in a thread of its own, says its phase and progress, writes its
result at its end, and ends whether a page is open or not. One search at a time for the app (a
search holds gigabytes, B2), three reads of sources at once, Apply and Restore at once, never
behind another tile's reads. **Stop** ends a task at its next step, at once
while it waits for its turn; what a stopped fetch did not get is no answer of the source
(`SourcePieces.fetch` raises `SYS_CANCELLED`); Apply says it cannot be stopped (`stoppable`) and
ends what it began. Quit stops them all. While a task of the tile runs, no Apply; while Apply
runs, nothing but the list's edits (`state.possible`). What follows a task (an outline's tick and
images) runs once it has ended, as the cloud's state then allows, never after a Stop or a failure
(`Tasks.start(then=)`). Deleting a tile stops its tasks and waits for its Apply. A task that fails keeps its error, which the
tile's answer gives (`failed`) until its next task, for the page to say it. A task writes only on
the list it began on: forgetting the tile moves the list's generation on, and a write begun
before writes nothing (`cloudlist.update(since=)`); a result for a cloud that left the list
meanwhile is dropped.

## 4. Finding and laying

### 4.1 The search

The tile is read as X-Plane draws it (R2) at the sharpest level whose pixel is at least 15 m
(`search_level`), and once more a level finer where the textures are that fine: the finer reading
finds the small cumulus, the coarser keeps the large pale veils, and the two are merged (B1). The
detector is OmniCloudMask (DPIRD, MIT), run with OpenCV's DNN (`clouds/model.py`): both its
networks for the first reading, the first alone for the finer one. A cloud is thick or thin cloud
of at least 2 ha with its shadow within 600 m, its outline one simple ring (B3); at most 200 per
tile, the largest first.

Each other source covering the tile is then judged under every cloud, at one level finer than the
search's where it has it (`screen`), and only where X-Plane shows the photo (`find._inside`: the
sea X-Plane draws alone is no part of a cloud):

- **no picture**: it covers less than 90 % of the cloud;
- **the same photo**: its picture under the cloud correlates with the tile's at 0.9 or more
  (ArcGIS sells Bing's pictures over much of the world): never proposed;
- **cloudy** or **clear**: the detector on its picture, clear when at most 10 % is cloud;
- its **fit**: how its picture correlates with the tile's on a band 400 m wide around the cloud
  (other clouds left out), and the colour match of that band. A source is compared with the
  photo itself (`Picture.photo`), not with what X-Plane shows of it over water: the photo is what
  it replaces.

The best source is the clear one with the best fit.

### 4.2 What a source can answer

Per piece (`clouds/sources.py`): a picture; none there; it did not answer (a timeout, the network,
a server error); it refused (403, 429, its pushback); not a picture (a page). Only a picture or
none is kept: asked again, the others may answer (`sources.ASK_AGAIN`), and a cloud waiting on one
of them offers to ask again.

### 4.3 Getting a cloud's images

The check (`find.check`): the source's pieces at the level each texture the cloud reaches reads
it at, frozen into parts (2.5); Apply's own layer made from them and reduced to the map's level
(R5); it must cover 90 % of the cloud; the detector says clear or cloudy at the tile's sharpness;
the colour match is taken on the band around the cloud at the screening's reading, which answers
too: no picture or the same photo there, and the check says so rather than lay colours it could
not match. The search and the check judge by the same rules, each said once in `find.py`: the
box a source is read on, the band's width, the level it is judged at, what the band leaves out,
the cloud's core, what pieces that did not come answer. Its result is the cloud's entry (2.2),
with the digest of each texture as built it was got on, kept in the list under that source; its
preview is made when After first shows it.

### 4.4 Laying in a texture

`laying.lay_texture`, from the texture as the build made it: its first level decoded; each cloud
laid over it in the list's order (`clouds/layer.py`): inside the outline the source's pixels,
centre on centre, in the colours of `match`; a source four times or more coarser than the texture
sharpened a little. The edge is soft (24 px), drawn past the window and the tile's edge before it
is blurred, so a seam between textures is no edge of the cloud. Where the source has no picture
nothing is laid: a hole is worse than a cloud. The texture's levels are then made again from the
laid first level as the build makes them (`mips.mip_chain`, `gamma22`), and in each level only
the blocks whose pixels a cloud reaches are encoded again, as the build encodes them
(`encode.encode_blocks`); every other block is the build's. A block of one level is two by two
blocks of the level above, so the blocks a cloud reaches are known level by level. Apply with
two clouds over three textures of 4096 px: 1.8 s (B4).

## 5. Failures

### 5.1 A source that cannot give its images

A source with no picture this sharp there, or showing the same photo, may no longer be the cloud's
source: its menu greys it with what it answered, and the cloud takes the best other one, else
waits for a source. No answer, refused for now, not pictures: the row says so and **Get its
images** asks again. A source removed
from the registry keeps its held clouds: they lay from their parts.

### 5.2 A write cut short, a file gone

Apply is all or nothing (2.3); only a power cut in the instant its textures are put in place can
leave some of them new, each whole and saying what it shows: the rows say which clouds the tile
shows whole, and Apply again ends it. A build stopped or failed lays nothing again; its next one
does. On Windows, a texture X-Plane holds cannot be replaced: Apply says so and changes nothing
(`XP_PACK_CONFLICT`: quit X-Plane and apply again). A texture as built missing from `clouds/` is
taken from the build's store when it is there, else said (`CLOUD_TEXTURE_LOST`): building the
tile again brings its textures back as built, its
clouds with them where they stand; then Apply or Restore again. A part found nowhere is no error:
its cloud waits for its images (R6).

### 5.3 What else is refused

`CLOUD_TILE_NOT_READY` (the tile is not ready, with its state), `CLOUD_NOT_BUILT`,
`CLOUD_UNKNOWN` (no such cloud), `CLOUD_INVALID` (an outline only is removed, a polygon too small),
`CLOUD_DETECTOR_MISSING`, `CLOUD_TEXTURE_LOST` (5.2), `CLOUD_TEXTURE_UNREADABLE` (2.1). Their
words: `docs/specs/errors.md`.

## 6. The routes

| Route | What |
|---|---|
| `GET /api/clouds` | The Library's column: by row path, the rows it is offered on (1.2), what the tile holds and what waits |
| `GET /api/clouds/{tile}` | The view (1.5): the tile's state and line, what it allows (`allowed`), the words of After and Before (`views`), whether something is under way (`busy`), its picture, every row with its line and tone, its actions, what a click on it does (`toggle`), its task and After's preview of it, the count and the questions (`texts`), the tile's own tasks, its last failure |
| `POST /api/clouds/{tile}/find` | Starts a search |
| `POST /api/clouds/{tile}/picks` | `{id, tick?, source?}`: a tick or a source, as the cloud's state allows; gets the images when its state asks them |
| `POST /api/clouds/{tile}/check` | `{id}`: gets the cloud's images again |
| `POST /api/clouds/{tile}/outlines` | `{polygon: [[lon, lat], ...]}`: an outline, its sources tried |
| `DELETE /api/clouds/{tile}/outlines/{id}` | Removes an outline |
| `POST /api/clouds/{tile}/discard` | The list back to what the tile holds |
| `POST /api/clouds/{tile}/apply` | `{restore?}`: starts Apply (or Restore), a task; answers `{task}` |
| `GET /api/clouds/{tile}/files/{name}` | The picture or a preview |
| `POST /api/clouds/tasks/{id}/stop` | Stops a task |

## 7. Not done

- Clouds replaced with the 1.4.0 test versions (rc) are not carried: the next build of such a
  tile brings them back as photographed, and their lists are put aside.
- Find and Apply act on one tile at a time.
- A tile installed by copy (`osxp install --copy`, the command line only): X-Plane reads the copy,
  which Apply does not change; the next install copies the textures again, the clouds in them.
- A cloud over the sea is laid sharp where the build blurs the sea (`sea_texture_blur`); X-Plane
  shows the photo there at the sea mask's share only.
- A build on the command line beside the app is not seen as a build by the cloud mode: Apply's
  check of each texture's identity (2.3) is what keeps them apart.
- The frozen parts in `<data>/clouds/parts` of clouds never applied stay until deleted by hand.

## 8. Code

| Module | What |
|---|---|
| `clouds/layer.py` | a cloud's patch, its layer, laying, parts |
| `clouds/tile.py` | what a tile shows (2.1), a texture's stamp, `stands_not` and `lays_not` (R6), the tile's own folder, the paths of the cloud files |
| `clouds/laying.py` | laying a texture (4.4), the folder made to show clouds, Apply, Restore and a build after (2.3) |
| `clouds/picture.py` | the tile as X-Plane draws it (R2) |
| `clouds/find.py` | the search, the judging and the check (4) |
| `clouds/sources.py` | the other sources' pieces (4.2) |
| `clouds/cloudlist.py` | the list (2.4, R3) |
| `clouds/state.py` | every rule of the mode: the tile's and each cloud's state, the view the page shows (1.5, R4) |
| `clouds/tasks.py` | the tasks (R7) |
| `clouds/model.py`, `clouds/opencv.py` | the detector and OpenCV, downloaded once |
| `api/clouds_api.py` | the routes (6) |
| `pipeline/build.py` | `_clouds_again`, the build's one call to the clouds (2.3) |
| `ui/clouds.js` | the cloud mode of the page: it shows the view, decides nothing |
