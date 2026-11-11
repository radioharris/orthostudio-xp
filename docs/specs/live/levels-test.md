# Which levels X-Plane takes, and when (20 October 2026)

X-Plane 12.4.4-b4 (Apple Silicon, Metal), macOS 26.6.2, M4 Pro 48 GB, a 2,704 px wide image with a
65° field of view, texture quality Maximum. Live on branch `live-mesh`, its folder on the external
SSD, zoom 16/18. Starts at LSZH runway 16 (a Cessna 172 placed by X-Plane's web API) and at LSGG
runway 04 (from X-Plane's menu).

## The rule

- **X-Plane takes each texture's level from the distance between the aircraft and the texture's
  centre**, in the rings of [probe-flight-1.md](probe-flight-1.md) times 1.13 (below, 2026-10-22):
  for a ZL16 texture 4096 px under 13.6 km, 2048 px to 23.7 km, 1024 px to 42.9 km, 512 px to
  81.4 km, 256 px beyond; for a ZL18 texture the same four times closer.
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
every one neutral there until the aircraft moved. Since 2026-11-11 the plugin's position, far from
the start expected and not where the same X-Plane flew last as that start was chosen, replaces it
(`live.md`, the expected start).

## The rings 12 % farther (2026-10-22, KOWB)

A start at KOWB runway 06 (an A320, X-Plane's image 2,704 px and 65° as above) showed neutral
textures 20 to 45 km from the aircraft that Live had built a level coarser than X-Plane took:

- **With the camera** over them: ZL16 textures at 21.4 and 21.6 km (centres) showed their 2048 px
  level, neutral, one at 24.7 km its 1024 px; at 40.6 and 42.0 km their 1024 px, neutral, one at
  46.4 km its 512 px; four ZL18 ones at 18.7 to 21.4 km more than their 256 px. In a run with every
  level built and tinted to 57 km, the same ZL16 at 21.6 km showed blue (2048 px), at 26.9 km
  yellow (1024 px); and no texture was read again while the camera went 5, 26 and 45 km out
  (access times reset first): the camera makes X-Plane read nothing.
- **Counting X-Plane's opens** of every built texture of the pack while it loaded the flight, each
  marked unread again after each read (`opens.py` in the session's scratchpad): at a start X-Plane
  opens a texture for 256 px at its DSF's load and once more, straight to its level, if it wants it
  sharper. The second open came for every ZL16 texture whose centre was within 80.9 km and for none
  from 80.8 km on (three in between), for every ZL18 one within 20.0 km and none from 20.3 km on:
  four times closer, as the law says, but 12 % past 72 and 18 km. Counted to the nearest edge
  instead of the centre, the ZL16 split is twice as wide (six textures in 0.4 km).

So every ring is the first probe flight's times 1.13 (`rings.RINGS_ZL16_KM`, 80.9 / 72 = 1.124
rounded up): the 2048 and 1024 px points seen with the camera fall within it. Why the probe flight
read them nearer is not known: it measured in flight, a Cessna, the same image.

In flight Live builds each ring wider, to keep ahead of the aircraft: 1.25 times the probe flight's
until then, 11 % past the true rings, which every flight had flown with (0 texture late near the
aircraft in the campaign of 2026-10-17). The margin is now 1.11 (`rings.DEFAULT_MARGIN`): in flight
the same distances as before to 0.3 %, 1.13 x 1.11 against 1.25; only the starts, built without
it, changed.

Not explained by it: a ZL16 texture at 29.1 km, built at 1024 px, showed neutral at the first
start of that morning, its neighbours not; that start followed a pack set up again from scratch.

