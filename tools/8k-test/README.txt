OSXP 8K test converter (experimental, for measuring only)
==========================================================

What it does
------------
For every OrthoStudio XP tile you give it, it writes two new packs:

  OSXP 8K test/4K/zOSXP_4K_<tile>   your tile's own 4K photos
  OSXP 8K test/8K/zOSXP_8K_<tile>   the same photos glued four by four into 8K textures

Both copies get the same treatment otherwise (same DSF, rewritten the same way through
DSFTool), so they differ only by their textures: 4 times fewer textures and fewer terrain
files in the 8K one. The photos are not recompressed, the quality is the same.
Your original tile is only read, never changed.

The "OSXP 8K test" folder is created next to the folder that really holds your tiles (it
follows the links OrthoStudio XP puts in Custom Scenery). The 4K copy takes no extra disk
space (its photos are links to your tile's). The 8K copy takes at least the size of the
tile's photos, more on a coast: a quarter without photo still takes its place, and an 8K
with water in one quarter is stored in the heavier format with transparency.

It needs OrthoStudio XP installed on the computer: it uses its programs, nothing else to
install.

How to run it
-------------
Keep all the files of this folder together, then:

  Windows  double-click "OSXP 8K test.bat"
           (or drop the tile folders onto it)
  macOS    double-click "OSXP 8K test.command"
  Linux    run ./osxp-8k-test.sh in a terminal

A window asks for the tiles: drag your tile folders (zOrthoStudio_+43+007 ...) from
Custom Scenery into it, press Enter. About a minute per ZL16 tile.

The first time, the system may refuse a file downloaded from the internet:
  Windows  "Windows protected your PC": More info, then Run anyway.
  macOS    "Apple could not verify...": System Settings, Privacy & Security,
           Open Anyway next to "OSXP 8K test.command", and confirm.

How to compare
--------------
1. Put the 4K packs in Custom Scenery (a link is enough), untick your original tiles in
   scenery_packs.ini, start X-Plane, measure.
2. Swap: the 8K packs instead of the 4K ones, X-Plane restarted, measure again.
   Same aircraft, same spot, same time of day, clear sky (clouds change FPS by several
   frames on their own).
3. When done, remove the test packs, delete the "OSXP 8K test" folder and tick your tiles
   again.

What it refuses
---------------
- Tiles built with "imprint masks to DDS" turned off (sea masks in separate files).
- Textures that are not 4096 px DXT1/DXT5 with all their mipmaps.
- Anything whose texture coordinates would not land on their ground after the change
  (it checks before writing, and writes nothing for that tile).
- A tile whose copies are already there: move the old ones away first.

Tested on one OrthoStudio XP tile (+43+007, Bing ZL16) on macOS and on Windows 11, the same
files on both; the Linux launcher only on a simulated install.
