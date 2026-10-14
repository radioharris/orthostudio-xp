# Witness tiles

What a change touches, known before it is released: a fixed set of tiles built with the last
release and with the branch, compared tile by tile. Tool: `tools/witness/witness.py`, list:
`tools/witness/tiles.txt`. Tests: `tests/test_witness_tool.py`.

## 1. Why

A fix of the engine runs on every tile. Most of the time it changes only the tiles where the fault
was; sometimes it changes many tiles a little (1.0.2's join of near nodes moved the height of water
by a few centimetres on most tiles). Until now each fix was checked by a campaign written for it,
which took an evening. The witness tiles answer the same question in one command, the same way
every time: which tiles changed, where, and by how much.

## 2. What it proves, and what it does not

* **The fixed list** (`tiles.txt`, 72 tiles) holds every tile that ever showed a bug, and tiles of
  every kind of ground: coasts, deltas, lagoons, islands alone in the sea, high mountains, big
  cities and airports, deserts, the far north and south, the antimeridian. A tile that shows a new
  bug is added and never leaves. Every tile needs X-Plane's own Global Scenery (not its Demo Areas),
  the default relief.
* **The random draw** (`--random N --seed S`) adds N tiles drawn among X-Plane's Global Scenery,
  the same on both sides for the same seed. No problem on N random tiles only says that fewer than
  about 3 tiles in N are touched (with 95 % confidence): it says nothing about rare cases.
* **Rare cases** are found by counting, over the whole planet, where the condition a fix acts on
  occurs, without building anything (the 16 runways of 1.0.1). That is done fix by fix.
* **The terrain and the masks**: the tiles are built to their DSF, without imagery and without
  the overlay. The water masks, which say where the photo shows over the water near a shore, are
  built and compared too. A change of the photos or of the overlay is not seen here.

## 3. Running it

The builds go through the app's own path at a new user's settings (`make_specs`, then
`build_tiles`), with the map library copied from the real settings (never printed) and X-Plane's
folder from them. `OSXP_HOME` must be a folder of its own; the cache there is emptied after each
batch of 8 tiles. A tile already in `--out` is kept, so a stopped run goes on where it stopped.

```bash
cd <checkout of the release>
OSXP_HOME=/tmp/witness-home .venv/bin/python <branch>/tools/witness/witness.py build --out ~/Desktop/Dev/osxp-witness/v1.0.2
cd <checkout to check>
OSXP_HOME=/tmp/witness-home .venv/bin/python tools/witness/witness.py build --out ~/Desktop/Dev/osxp-witness/<branch>
.venv/bin/python tools/witness/witness.py compare ~/Desktop/Dev/osxp-witness/v1.0.2 ~/Desktop/Dev/osxp-witness/<branch> --report report.txt
```

Measured on the reference Mac (2026-10-13): 72 tiles in 14 minutes, 2.1 GB kept per
version (the meshes). `build --digests-only` keeps the digests and the counts without the mesh
and the masks themselves (a few kilobytes a tile): enough to say which tiles are identical over
thousands of tiles; a tile that changed is then built again without it to see where and how. The
reference of a release is built once, outside the repository, and kept until the next release.

## 4. Reading the report

The first lines count the tiles **identical** (the vector files, the mesh, the masks and the DSF
have the same bytes as the release's), **changed**, **failed** (built by the release, not by the
branch) and **fixed** (the reverse). *To look at* lists what deserves a look before a release:

* **land surface moved** more than 1 m, where triangles have a new corner: the terrain itself
  changed there. Expected where the fix acts, a question anywhere else;
* **crossings** of the vector graph up, **largest fan** more than doubled (and over 1 000),
  **needles** (triangles under 1 cm2) more than doubled, **land, inland water or sea** changed by
  1 ha or more.

Then one line per changed tile:

* **land cut again**: the land triangles found in one mesh only, gathered into cells of a hundredth
  of a degree, the largest cell named;
* **water cut again**: the same for water, counted only: big flat water triangles are cut again
  far around a change, which no one sees;
* **water heights**: the heights of water vertices standing at the same place in both meshes. The
  mesh smooths each body of water as a whole, so a change to a lake or a river moves its height a
  little everywhere along it;
* **land vertices**: the same for land vertices. Their height comes from the relief alone, so it
  should not move;
* **land surface (new points)**: how far one mesh's ground is from the other's, measured at the
  corners, edge middles and centre of every land triangle with a new corner;
* **masks**: the mask files that differ, with the number of pixels that changed over the tile,
  how many of them lie far from what the branch took out of the water (with `--pavement-rule`,
  section 5), and where. A mask cell runs beyond the tile's border, where it is black when no
  neighbour was built; no part of the tile's DSF uses those pixels, so they are counted apart.
  The same water cut into other triangles changes a few pixels at its edge: the masks are drawn
  triangle by triangle, outlines included (2026-10-14: 147 such places in 98 tiles, the water the
  same to the square metre around each);
* **diagonals turned**: the same for triangles whose corners are all in the other mesh. Four
  points on a circle, such as the corners of a rectangle, can be cut along either diagonal, and
  Triangle picks by the order it reads the points in. A change anywhere in a tile can turn such a
  diagonal elsewhere in the same tile, never in another tile. Both are the same relief cut two
  ways, but on a slope the middle of a large rectangle can differ by tens of metres (38 m in the
  Hakone mountains, 60 km from a change at Haneda).

The report also goes to `<report>.json`, every number of every tile.

## 5. A rule that must act in one place only

Some fixes are meant to change only the tiles where their condition occurs. The rule that takes
X-Plane's pavement out of the water (`docs/specs/xp-pavement.md`) writes what it took out next to
the vector files (`xp_dry_sea.wkb`, `xp_dry_inland.wkb`), and the record keeps it with the
airports concerned. With `compare --pavement-rule` the report then counts the changed tiles where
pavement was taken out, the changed tiles next to one of them built in the same batch (their masks
read the neighbours' meshes, so a mask cell that runs over the border can change), and the changed
tiles anywhere else, which must be none. *To look at* adds:

* **changed, yet no pavement was taken out of the water in this tile**: the vector files or the
  mesh of such a tile changed;
* **masks changed, yet no pavement was taken out of the water here or next door**, and **the DSF
  changed alone**;
* **masks changed farther than 200 m from the pavement taken out**: mask pixels that changed far
  from what was taken out, in the tile or next door (the masks fade over 100 m from the water's
  edge);
* **pavement taken out of the water, yet nothing changed**.

The tiles to build for such a rule come from a count over the whole planet of where its condition
can occur, generous on purpose, together with the witness tiles.

## 6. Limits

* Built by one Mac: the random draw and the scenery are those of its X-Plane.
* The surface is compared at sample points, not over every point of the tile; two surfaces of
  triangles differ most at corners and where edges cross, which the samples come close to.
