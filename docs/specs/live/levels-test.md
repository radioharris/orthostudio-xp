# Which levels X-Plane takes, and when (20 October 2026)

X-Plane 12.4.4-b4 (Apple Silicon, Metal), macOS 26.6.2, M4 Pro 48 GB, a 2,704 px wide image with a
65° field of view, texture quality Maximum. Live on branch `live-mesh`, its folder on the external
SSD, zoom 16/18. Starts at LSZH runway 16 (a Cessna 172 placed by X-Plane's web API) and at LSGG
runway 04 (from X-Plane's menu).

## The rule

- **X-Plane takes each texture's level from the distance between the aircraft and the texture's
  centre**, in the rings of [probe-flight-1.md](probe-flight-1.md): for a ZL16 texture 4096 px
  under 12 km, 2048 px from 12 to 21 km, 1024 px from 21 to 38 km, 512 px from 38 to 72 km, 256 px
  beyond; for a ZL18 texture the same four times closer (4096 px under 3 km, 2048 px to 5.25 km).
- **It takes them when it loads a flight, and again as the aircraft moves.** It reads nothing
  again when only the camera moves, in the drone view or the circle view, kilometres out and near
  the ground.
- **The screen shows the sharpest level X-Plane holds that the camera's distance calls for.** A
  camera taken far from the aircraft shows the ground a little blurry. A level X-Plane took before
  Live built it holds the neutral colour, which shows once a camera comes close enough to call for
  that level: from the cockpit it may not show at all.

## How it was measured

**Access times.** The disk sets a file's access time at its first read after a write, a read
through a mapping too (checked on the external SSD, APFS). `build/measure/reads.py` (not part of
the product) scans the pack's built textures every second and writes down each write (mtime) and
each first read after it (atime), with the aircraft and the camera. Unbuilt textures are hard links
of one neutral file, left out. Touching a file (`utime`) resets it, so that its next read shows.
`fs_usage` was given up: X-Plane maps the files, and the neutral levels, shared by every texture,
are read from the disk once and then come from memory.

**Tinted levels.** With `OSXP_LIVE_TINT_LEVELS=1` in Live's environment, its 4096, 2048 and 1024 px
levels are washed red, blue and yellow (`orthostudio.live.texture.TINT_LEVELS`). A camera taken
over a texture shows the colour of the sharpest level X-Plane holds of it; the neutral colour, a
level it took before Live built it.

## Results

| test | what X-Plane read |
|---|---|
| LSZH start, Live from nothing, X-Plane held 33 s | in the 70 s after it went on, 3,426 textures once, all after Live wrote them: 62 of the 63 within 21 km, 566 of the 568 within 72 km, 1,660 of 1,794 from 72 to 150 km; none of the 274 Live wrote after it went on (270 farther than 230 km) |
| aircraft standing, drone camera taken 25 km out near the ground, 4 min | nothing, of 185 textures Live wrote for the camera meanwhile |
| aircraft standing, circle view 4.7 km out, then to 21.5 km | nothing |
| aircraft moved 15 km by the API (paused), over Winterthur | 91 textures within 6 to 30 s, each in the ring of its level from the new place; 77 of them Live had written for the camera before |
| LSGG start, tinted levels | red under 12 km of the aircraft (a texture at 8.7 km), blue beyond (14 km), as the rule says |

## The neutral colour at LSGG

Live plans a runway start from the airport's own point in `apt.dat` (its datum), 2 km from the
threshold of LSGG's runway 04 where X-Plane places the aircraft. A texture whose distance crosses a
ring between the two points is taken one level sharper by X-Plane than Live built it: the Crêt d'Eau
(`23264_33824_BI16`) is 20.2 km from the threshold (2048 px) and 22.0 km from the datum (1024 px).
Its 2048 px level was neutral, grey-green with the camera over it, every time. Eleven ZL16 textures
are so for that start. At 14:05 the same took a ZL18 texture 1.5 km from the aircraft and 3.5 km
from the datum at 4096 px before Live built it. Since, a start on a runway or a stand is planned
from its threshold or the stand in the `apt.dat` X-Plane loads it from, a custom scenery's listed
above Global Airports first (`orthostudio.live.route.start_spot`, `airport_files`), where X-Plane
places the aircraft (45 m from LSGG's threshold of runway 04); the airport's point serves only
for an airport without them, or before Live's index of `apt.dat` is read (4 s at its start). The
plugin's position, read as X-Plane holds, is not used: a flight chosen while another flies still
gives the old one there.

## A start taken for the previous one

X-Plane relaunched with its saved start (LSGG runway 04, `Freeflight.prf`) while a flight was placed
at LSZH through the web API: Live built round Geneva and X-Plane loaded Zurich before its textures,
every one neutral there until the aircraft moved.
