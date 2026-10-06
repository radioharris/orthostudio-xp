# The workshop: build in one place, keep your tiles anywhere

You rarely know, when you build a tile, where you will want to keep it. Maybe on the external
disk with the rest of the Alps, maybe in a folder for next week's trip. You no longer have to decide
before you build. Every tile is built in one place, the workshop, and you file it wherever you like
afterwards, on any disk. X-Plane follows it there.

## The workshop

The workshop is the data folder chosen in Settings (`.orthostudio` in your user folder unless you
chose another). New tiles are built there, next to the cache: the downloaded images, the relief,
the map data and every intermediate result. The cache is why a second build of a tile is quick:
nothing is downloaded or computed twice.

Builds read and write a lot, so keep the workshop on a fast disk, an SSD if you can. The tiles
themselves can go anywhere.

## Filing tiles

1. Open the Library.
2. Tick the tiles you want to file. *Pick all shown* ticks every tile left on screen by the search
   and the folder list.
3. Click *File elsewhere…* and choose a folder.

Before anything moves, OrthoStudio XP tells you what it is going to do with each tile. On the
workshop's own disk a tile is moved, which is instant. On another disk it is copied, the
copy is read back file by file, and only then is the tile removed from the workshop. During the
copy a line under the search shows how far it is, with a *Stop* button; the tile being copied stays
where it was.

Afterwards the Library lists the tile in its new folder and X-Plane reads it from there. Its roads
and forests go with it, and a tile you had turned off in X-Plane stays off.

X-Plane must be closed. Tiles are filed between builds: while a build runs, *File elsewhere…* is
greyed out until it ends.

When your tiles are in more than one folder, the Library writes the folder under each tile
(*Workshop* for those still in the workshop), and the *Folder* list beside the search shows one
folder at a time.

## Building a filed tile again

Choose it on the Plan as usual, for instance after changing a setting. It is built in the workshop,
next to its cache, and then put back in its folder in place of the old version. The old version is
only removed once the new one is in place. If nothing changed, the build takes a few seconds and
nothing is copied.

If the tile's disk is not plugged in, the tile is not built, and Works says why.

## Getting room back on the workshop's disk

The workshop keeps the cache of the tiles you filed elsewhere, so that building them again stays
quick. If you need the space: Library, *Free space…*, and tick the line of the tiles filed outside
the workshop. Their downloaded images and data are deleted. The tiles themselves are not touched:
they stay complete and in X-Plane. Building one of them again later downloads its images again, as
the first time.

Two things stay. Their mesh, about 240 MB a tile, because a neighbouring tile built later reads it
to draw the shores along their common border. And for tiles filed on the workshop's own disk, the
tile and the cache share the same files, so that part of the room does not come back.

## Moving a tile yourself

You can also move a tile's folder yourself, in the Finder or the File Explorer. The Library then
shows the tile as *Not found*, with a *Find again…* button. Point it to the folder you put the tile
in, and the Library and X-Plane follow. Nothing is copied. X-Plane must be closed.

Do not put a tile inside X-Plane's own `Custom Scenery` folder: X-Plane reaches your tiles through
links placed there, and OrthoStudio XP refuses that folder.

## A disk that is not plugged in

A tile whose disk is not plugged in shows *Disk not connected*. Plug the disk in and the tile comes
back by itself. Do it before you start X-Plane: started without the disk, X-Plane drops the tile
from its list, then adds it back at the bottom, where another pack's mesh may hide it.

## Limits

- Only tiles built by OrthoStudio XP can be filed. Tiles built with Ortho4XP stay where they are:
  OrthoStudio XP lists them, adds them to X-Plane and takes them out, but never moves or deletes them.
- A tile with a missing file is refused until you build it again, which is quick from the cache.
- Folders inside the workshop, inside a tile's own folder or inside X-Plane's `Custom Scenery` are
  refused. A tile is not filed into a folder that already has a folder of its name.
- A filed tile does not need the cache to work in X-Plane. The cache only makes the next build of
  it quicker.

More detail on what is kept on the disk and why:
[how-it-works.md](how-it-works.md).
