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
- **Replaced / remplacé** (held, in the code): a cloud the tile's textures were made with.
- **Undone** (in the code): a held cloud a build did not lay again, the photo under it having
  changed (R6); on the page, a ticked cloud waiting for its images to be got again, its line
  saying why.

### 1.2 The Library

A column **Clouds**: the button **Clouds…**, which opens the cloud mode on the tile, and in a few
words what the tile holds and what waits: "3 replaced", "2 to apply". A tile with no list yet
shows the button alone.

### 1.3 The cloud mode

The Plan's map, brought over the tile; the Plan's steps make way for the cloud panel.

- **On the map**: the tile drawn as X-Plane draws it (R2). In **After**, each ticked cloud's
  preview over it, in the list's order; in **Before**, none. Every cloud's outline: yellow when
  unticked, the accent when ticked, the current one thicker. A click on an outline ticks or
  unticks it.
- **The banner over the map**: **After**, **Before**, **Outline a cloud**, the sentence of the
  view ("What X-Plane shows.", "What X-Plane will show after Apply.", "The tile with no cloud
  replaced."), **Back to the Plan**.
- **The panel**: the tile's name; the tile's line when it is not ready (1.5); each task under way
  with its phase, its progress and **Stop**; the list, a row per cloud; the count of what Apply
  changes ("To replace: 1 · Back as photographed: 2"); **Find clouds**, **Apply (n changes)**,
  **Discard changes**, and **Restore the original photos** when the tile holds replaced clouds.
- **A row**, laid out as the Plan's zones: its tick and name, its area, **Show** (the map over
  it), a trash for an outline the tile does not hold; the menu of its sources, each with what it
  has there and its fit ("Esri World Imagery · clear · fit 93 %"), the best first, those that
  cannot be chosen greyed (`sources.PROPOSABLE`: the same photo, no picture, not pictures); its
  line (1.5); **Get its images**
  when they are not in; **Stop** while a task of it runs.

### 1.4 What the user does

| Action | What happens |
|---|---|
| **Find clouds** | The search (4.1), one at a time for the app; the clouds merged into the list (R3). The first one downloads the detector, its size asked first |
| Tick, a click on an outline | The cloud ticked with its source (the best when none was chosen); its images got when they are not in |
| Choose a source | Ticks it with that source; its images got |
| **Get its images** | Asks the source again (after no answer, a refusal, or a photo that changed) |
| **Outline a cloud** | Points clicked on the map, a double-click or Enter ends it, Escape gives up. The found clouds it covers that are neither ticked nor held leave: it is them. Its sources are tried and it comes ticked with the best |
| Trash of an outline | Removes it from the list; an outline the tile holds has none: unticked, it comes back at Apply, and its trash comes then |
| **Apply** | Asks first, saying what changes and what waits; builds the tile as built with the list's ticked clouds (R4) |
| **Discard changes** | The list back to what the tile holds |
| **Restore the original photos** | Asks first; every cloud unticked and the tile built without any |
| **Stop** | Ends the task at its next step, the list as it was |

### 1.5 The states

The tile's state, the first that holds (`clouds/mode.py`, `tile_state`):

| State | When | The tile's line |
|---|---|---|
| `data_away` | the data folder's disk is not connected | the map, finding and Apply come back with it |
| `disk_away` | the tile folder's disk is not connected | |
| `in_build` | the tile is being built | changes made now wait for the next Apply |
| `queued` | it waits in the queue | the same |
| `not_built` | no folder of ours for the tile | |
| `earlier_version` | its textures do not say what a cloud is chosen on, or no settings of its last build are kept (2.5) | build it again once with this version |
| `own_source_gone` | its source is no longer in the sources | add it back in Settings to apply |
| `ready` | | |

A cloud's row, the first that holds (`rows`): read only while the tile is not ready, in a build or
queued; a task of it (getting its images, trying an outline's sources); **held** (replaced with
the source ticked); **back** (replaced, unticked: it comes back at Apply); **ready** (ticked, its
images in: replaced at Apply, or "EOX now, Esri after Apply"); **waiting** (ticked, its images
not in, with why: not got yet; the photo under it changed since, its level, source, textures,
images or colours; or what the source answered, 5.1); **found** (unticked). A held cloud a build
undid is a ticked cloud whose images no longer stand (R6): its waiting line says why, "The tile's
level changed since its images were got: get them again", and nothing else says it.

## 2. What is kept

### 2.1 What the tile holds

The clouds a tile shows replaced are the clouds its textures were made with. The build that makes
them writes them in the textures artefact's own `manifest.json`, beside the list of its textures:

```json
"clouds": {"applied": [<cloud entry>, ...], "undone": {"<id>": "level"}}
```

with, per texture, `chunks` (the digest of the tile's own pieces it was made of) and, for the
tile, `colours` (`{"tile": [b, c, s], "zones": [[ring, b, c, s], ...]}`). The pack places that
description in the tile's folder as `clouds/<textures key>.json`, with the artefact's
`clouds/parts/` and `clouds/originals/textures/` (`pack.CLOUD_FILES`), before the
`orthostudio.toml` that names the textures, and once it is written removes what belongs to no
textures it names. What the tile holds is read from its folder alone (`tile.read_tile`), for the
key `orthostudio.toml` names: it cannot disagree with what X-Plane shows, a build stopped half
way leaves the folder naming the description before, and the tile keeps its clouds whatever the
store loses (Free space, another data folder). The `tile.textures` rule is at version 2, so a tile
built earlier is described at its next build (its textures themselves are store hits).

### 2.2 A cloud entry

```json
{"id": "z9lo5c8m", "source": "Arc", "match": [[1.03, 1.03, 1.18], [57.1, 52.8, 47.5]],
 "rings": [[lat0, lon0, lat1, lon1, ...]],
 "parts": {"5008_2224_BI14": [15, "<part digest>"]},
 "images": {"5008_2224_BI14": "<the tile's pieces digest>"},
 "colours": <the tile's and zones' colours within 1 km of it>}
```

`match` is a gain and a shift per channel, the source's colours brought to the photo around the
cloud; `parts` per texture it reaches, the level the source is read at and the frozen part (2.3).

### 2.3 Frozen parts

A part is the source's pieces a cloud reads in one texture (`OSXPPRT2`, `textures/clouds.py`),
written when its images are got, in `<data>/clouds/parts/<digest>.part`, copied into the
textures artefact's `clouds/parts` by the build that lays it, and from there into the tile's
folder (2.1), where every later build of the tile finds it. A build never asks a source: it lays
parts. The texture rule's input `clouds` (`OSXPCLD3`) carries a texture's patches and their parts.

### 2.4 The list

`$OSXP_HOME/clouds/<tile>.json`, format `osxp-cloud-list-1`: every cloud with its id (eight
characters, never given again on that tile), its number, its kind (`found`, `drawn`), its
outline, its area, what each source has under it (`screening`, the search's), its best source,
its tick, its source, and per source tried its check (its answer, fit, entry and preview). Only
`clouds/cloudlist.py` writes it (`pick`, `merge_search`, `add_outline`, `remove_outline`,
`record_screening`, `record_check`, `discard`, `restore`). The
list is the draft: each change is written as it is made, one writer at a time per tile, and
survives closing, quitting and every build. Its order is the laying order. A list that cannot be
read is moved aside (`.broken`) and made again from what the tile holds, its clouds ticked.
Deleting the tile, in the app or with `osxp uninstall --delete`, deletes its list and its
previews when the folder deleted is the one its clouds are kept for (`cloudlist.forgetting`).

### 2.5 The tile's build settings

`build_settings.json` in the tile folder, format `osxp-build-settings-1`, written by each
finished build: the key of the textures it made, its source, level, overlay and XP12 rasters
choices, and its settings without the computer's own (`COMPUTER_CONFIG_KEYS`: the OSM library's
address and key never go in a folder that may be shared). Apply builds the tile with them. They
count only while the tile's pack names those textures: a later build that did not write them
(another version, another program) leaves the tile to be built once again (`earlier_version`).

### 2.6 Caches

`<data>/clouds/<tile>/`: the tile's picture (a JPEG named by its textures and level) and the
previews (PNG named by their digest). Every path of the cloud files is in `clouds/tile.py`; the
folder's layout in `pipeline/pack.py` (`CLOUDS_DIR`, `CLOUD_FILES`). The sources' pieces are kept as a build's parent tiles
(`ParentCache`, per source). The detector's classes of the tile's last search are kept in memory
for an outline. Every one of them can be deleted: it is made again.

## 3. The rules

### R1. The tile holds its clouds

Every build of the tile lays its held clouds again, whatever starts it: the Plan, the Library,
Works, the command line (`BuildSpec.clouds` is `None` there and the build reads the tile's own,
`_with_held_clouds`). Only Apply gives other clouds (a tuple). A job is saved without its clouds,
so **Build again** in Works never replays an Apply.

### R2. The map is the tile as X-Plane draws it

The picture is drawn from the tile folder's DSF, triangle by triangle, each with the texture its
terrain names, read at the level of its DDS that matches (`clouds/picture.py`). Land shows the
photo whole; the sea shows it at the texture's alpha (the sea mask) over X-Plane's water; inland
water at that alpha times the lakes' share (`ratio_water`, the DSF's eighth plane, through the
water transition ramp) over its water; X-Plane's water alone, no photo. A texture holding a
replaced cloud is read from its original (4.6), so Before is the tile with no cloud replaced.

### R3. A cloud is one place

A cloud keeps its id and its number for ever; neither is given again on the tile. A search merges
into the list: a found cloud over one that is held or ticked is that one (it keeps its tick, its
source, its outline); found clouds over each other are one; an unticked found cloud the new search
no longer finds leaves. An outline takes the place of the unticked found clouds it covers, half of
one or more inside it (`cloudlist._covers`). A cloud leaves the list only when the tile does not
hold it (`cloudlist.removable`), and every cloud Apply lays carries its kind and number into what
the tile holds, so a list made again from the tile is the same list.

### R4. The list is a draft; Apply builds what its question showed

Apply builds the tile with its own settings (2.5), the computer's keys of now, and the clouds of
`laid_after`: every ticked cloud the tile holds with that source as it holds it, every other
ticked one from its images; those still waiting are left out, and the question said so. Only the
textures under the clouds changed are made again; the rest are store hits.

### R5. What you see is what is laid

One function makes a cloud's layer on a texture (`textures/clouds.py`, `layer`), and one gives its
colours, matched to the photo and clipped to what a pixel holds (`Layer.rgb`); the build lays
them, and the preview is the same layer in the same colours reduced to the map's level, weighted
by its alpha in linear light (`find._lay_reduced`), laid where X-Plane shows that texture, over
water at the share X-Plane gives the photo. Measured: B5.

### R6. A cloud stands on the photo it was got on

`laying.standing` is the one rule, for the build and the list alike, given the photo's colours as
the tile's description says them: a cloud stands while the
textures it reaches are those it was laid in (the same level, source and layout), the colours of
the photo within 1 km of it are the same, and each texture was made of the same pieces of the
tile (`images`). The build also needs each frozen part. A cloud that no longer stands is not laid,
whole, and the artefact records why (`undone`); textures that came of other pieces are made again
without it. In the list it stays ticked and waits, its line saying why, for its images to be got
again on the new photo; one Apply then puts it back.

### R7. The engine's tasks; one program per tile

A search, getting a cloud's images and trying an outline's sources are the engine's tasks
(`clouds/tasks.py`): each runs in a thread of its own, says its phase and progress, writes its
result into the list at its end, and ends whether a page is open or not. One search at a time for
the app (a search holds gigabytes, B2), three image gettings at once. **Stop** ends a task at its
next step; Quit stops them all.

A tile is built by one program at a time (`pipeline/turns.py`): the build holds each of its tiles
by a file in the data folder's `building/`, its process's number in it. Another program asked to
build one of them while that process lives is refused before anything is written,
`SYS_TILE_BEING_BUILT`; a file left by a program gone counts for nothing. In the app, builds wait
in their queue.

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
  (other clouds left out), and the colour match of that band.

The best source is the clear one with the best fit.

### 4.2 What a source can answer

Per piece (`clouds/sources.py`): a picture; none there; it did not answer (a timeout, the network,
a server error); it refused (403, 429, its pushback); not a picture (a page). Only a picture or
none is kept: asked again, the others may answer.

### 4.3 Getting a cloud's images

The check (`find.check`): the source's pieces at the level each texture the cloud reaches reads
it at, frozen into parts (2.3); the build's own layer made from them and reduced to the map's
level (R5); it must cover 90 % of the cloud; the detector says clear or cloudy at the tile's
sharpness; the colour match is taken on the band around the cloud at the screening's reading,
which answers too: no picture or the same photo there, and the check says so rather than lay
colours it could not match. Its result is the cloud's entry (2.2) and its preview, kept in the
list under that source.

### 4.4 Laying in the texture rule

In `texture.dds`, after the tile's and its zones' colours and before the water mask: inside the
outline the source's pixels, centre on centre, in the colours of `match`; a source four times or
more coarser than the texture sharpened a little. The edge is soft (24 px), drawn past the window
and the tile's edge before it is blurred, so a seam between textures is no edge of the cloud.
Where the source has no picture nothing is laid: a hole is worse than a cloud.

### 4.5 Keys

A texture without clouds has no `clouds` input, and an absent late input stays out of the key
(`graph/keys.py`, `late_inputs`): only the textures a cloud reaches are made again. A texture with
clouds, and the tile's textures step given clouds, have the rule `CLOUDS_RULE` among their
parameters: a new way of laying makes them again, and nothing else (2 = the colours clipped
before they are blended).

### 4.6 The originals

For the textures holding a cloud, the build runs the plain textures again into the artefact's
`clouds/originals/textures/` (store hits, hard links): the map's Before and the photo the search
reads under a replaced cloud.

## 5. Failures

### 5.1 A source that cannot give its images

The row says what it answered: no picture this sharp here, the same photo, no answer, refused for
now, not pictures; **Get its images** asks again, the menu offers the others. A source removed
from the registry keeps its held clouds: they lay from their parts.

### 5.2 A build stopped or failed

The pack names its textures last: the folder keeps naming the artefact before, whose clouds are
what the tile holds. The list is untouched; Apply can be pressed again.

### 5.3 What else is refused

`CLOUD_TILE_NOT_READY` (the tile is not ready, with its state), `CLOUD_NOT_BUILT`,
`CLOUD_UNKNOWN` (no such cloud), `CLOUD_INVALID` (an outline only is removed, a polygon too small),
`CLOUD_DETECTOR_MISSING`; `SYS_TILE_BEING_BUILT` (R7). Their words: `docs/specs/errors.md`.

## 6. The routes

| Route | What |
|---|---|
| `GET /api/clouds` | The Library's column: per tile with a list, its state and counts |
| `GET /api/clouds/{tile}` | The tile's state and line, its picture, every row (1.5), the count, its tasks, the detector's state |
| `POST /api/clouds/{tile}/find` | Starts a search |
| `POST /api/clouds/{tile}/picks` | `{id, tick?, source?}`: a tick or a source; gets the images when needed |
| `POST /api/clouds/{tile}/check` | `{id}`: gets the cloud's images again |
| `POST /api/clouds/{tile}/outlines` | `{polygon: [[lon, lat], ...]}`: an outline, its sources tried |
| `DELETE /api/clouds/{tile}/outlines/{id}` | Removes an outline |
| `POST /api/clouds/{tile}/discard` | The list back to what the tile holds |
| `POST /api/clouds/{tile}/apply` | `{restore?}`: builds the tile; answers `{job}` |
| `GET /api/clouds/{tile}/files/{name}` | The picture or a preview |
| `POST /api/clouds/tasks/{id}/stop` | Stops a task |

## 7. Not done

- Clouds replaced with the 1.4.0 test versions (rc) are not carried: the next build of such a
  tile brings them back as photographed.
- Find and Apply act on one tile at a time.
- The frozen parts in `<data>/clouds/parts` of clouds never applied stay until deleted by hand.

## 8. Code

| Module | What |
|---|---|
| `textures/clouds.py` | a cloud's patch, its layer, laying, parts and the texture input |
| `clouds/tile.py` | what a tile holds (2.1), the tile's own folder, the paths of the cloud files |
| `clouds/laying.py` | `standing` (R6) and the build's clouds (`TileClouding`) |
| `clouds/picture.py` | the tile as X-Plane draws it (R2) |
| `clouds/find.py` | the search, the judging and the check (4) |
| `clouds/sources.py` | the other sources' pieces (4.2) |
| `clouds/cloudlist.py` | the list (2.4, R3, R4) |
| `clouds/mode.py` | the states and the rows (1.5) |
| `clouds/tasks.py` | the tasks (R7) |
| `clouds/model.py`, `clouds/opencv.py` | the detector and OpenCV, downloaded once |
| `api/clouds_api.py` | the routes (6) |
| `pipeline/turns.py` | one program per tile (R7) |
| `ui/clouds.js` | the cloud mode of the page |
