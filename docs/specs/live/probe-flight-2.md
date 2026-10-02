# Probe flight 2: the screen, and a texture that grows (27 September 2026)

> Measured with X-StreamOrtho, OrthoStudio XP Live's research project, whose probe kit is not part
> of OrthoStudio XP. Its commands and paths are X-StreamOrtho's.

X-Plane 12.4.4-b1, macOS, M4 Pro. ToLiss A320, cockpit view, texture quality Maximum. The probe pack
(35 tiles of `z_ao_eur`, checkerboard test textures). `python3 -m probe capture screen --swap 8k`,
the report and the run's settings beside this page, in `probe-flight-2/` (the 881 MB trace was not
kept). The trace began a few minutes before the flight loaded; the figures below count X-Plane's
reads from the flight's load on.

## The level X-Plane reads depends on the screen

The aircraft still at Lyon LFLL (45.7462 N, 5.0859 E), three phases. For each, the farthest
texture X-Plane loaded at each level (from the opens of that phase, distances from the aircraft):

| X-Plane's image | ZL16 4096 px | 2048 px | 1024 px | 512 px | ZL18 4096 px | 2048 px |
|---|---|---|---|---|---|---|
| window 1579 x 1055 px, field of view 65° | 8.3 km | 13.3 km | 23.2 km | 43.1 km | 1.9 km | 2.6 km |
| full screen 2704 x 1756 px, 65° | 11.2 km | 19.9 km | 37.0 km | 72.0 km | 2.6 km | 4.0 km |
| full screen, field of view 33.5° | 20.5 km | 39.2 km | 76.8 km | 150.6 km | 5.3 km | 5.6 km |

- The distances follow the width of X-Plane's image in pixels: x1.35 to x1.67 for a window 1.71
  times wider (the nearer levels are measured on few textures, 7 km apart, so coarsely).
- They follow the angular size of a pixel: x1.83 to x2.09 when the field of view goes from 65° to
  33.5° (tan 32.45° / tan 16.75° = 2.11).
- At 2704 px and 65° they are the first flight's rings (12, 21, 38, 72 km): the rings of
  OrthoStudio XP Live hold for that screen. For another, scale them: a 4K image (3840 px) reads
  the 4096 px level of a ZL16 texture to about 17 km, a 1440p one (2560 px) to about 11 km.
- X-Plane had read everything within a minute or two of each change, the aircraft still.

## X-Plane takes a texture that has grown

`--swap 8k` replaced 119 test textures ahead of the aircraft, from 4096 px files to 8192 px ones
(template C, its 8192 px level magenta), while X-Plane flew at 2,500 ft, 207 kt, north of Lyon.

- 79 were opened again after the swap: all 79 read past the size of a 4096 px file, their levels
  found where an 8192 px file has them. X-Plane reads a texture's size again at each open.
- Finest level read from them: 8192 px for 6, 4096 px for 24, 2048 px for 38, 1024 px for 11.
- Seen in the sim: whole textures magenta up close, their checker squares half the size of their
  neighbours', and red (the 4096 px level) farther on the same texture.

So a texture can start at 4096 px, what a start needs, and become 8192 px later, where X-Plane will
read it sharp: XPME's "load in normal resolution, then high resolution when loaded".
