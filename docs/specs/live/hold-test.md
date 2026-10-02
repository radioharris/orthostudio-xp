# Hold test, 26 September 2026 (X-Plane 12.4.4-b1, LFML runway 13L)

> Measured with X-StreamOrtho, OrthoStudio XP Live's research project, whose probe kit is not part
> of OrthoStudio XP. Its commands and paths are X-StreamOrtho's.

This session tested whether a plugin can hold X-Plane's loading until the streamer is ready
(see docs/probe.md). The flight started at LFML runway 13L from the main menu, with the streamer
running without `--from`. The area had been built by an earlier flight. The record and the report
are in `2026-09-26-hold-test/`.

## The sequence

Times are seconds after the test plugin started, during X-Plane's start-up, and include the 8 s
holds.

| s | what |
|---|---|
| +23.6 | `Freeflight.prf` written with `_last_start {"runway_start":{"airport_id":"LFML","runway":"13L"}}`: the click on Start Flight |
| +30.1 | message 115 (1), PLANE_UNLOADED, FMOD_BANK_UNLOADING, PLANE_LOADED (0), held. The position datarefs read 43.4382 5.2129, the start airport; at the main menu they were 0, 0 |
| +40.8 | FMOD_BANK_LOADED (0), held |
| +49.0 | FMOD_BANK_LOADED (1), LIVERY_LOADED (0), held |
| after that hold | `Init dat_p0 type:'runway_start' apt:LFML rwy:13L`, then the DSF loads, in Log.txt |
| +71.9 | SCENERY_LOADED, held: 38 DSFs were loaded before it |
| +89.9 | AIRPORT_LOADED, held: 34 more DSFs were loaded in between |
| +98.2 | the flight runs |

## What it shows

1. **The start is known when the user clicks Start Flight.** X-Plane writes it to `Freeflight.prf`
   at the click, 6 s before its first message to plugins. From that first message on, the position
   datarefs give the start.
2. **A plugin can hold X-Plane before it reads any scenery.** PLANE_LOADED, FMOD_BANK_LOADED and
   LIVERY_LOADED arrive before any DSF or texture is read. Holding each of them for 8 s held the
   load:
   - no Log.txt line and no scenery file opened during those holds;
   - `Init dat_p0` and the first DSF were logged only after the LIVERY_LOADED hold ended. Log.txt
     was 65,303 bytes long at the end of the hold, and the `Init dat_p0` line ends at byte 65,423.

   Holding there until the streamer has built the start area is what AutoOrtho does through FUSE.
3. **SCENERY_LOADED and AIRPORT_LOADED come too late**: SCENERY_LOADED arrives once most DSFs are
   loaded, and AIRPORT_LOADED once all are.
4. **X-Plane's main thread writes Log.txt**: the file does not grow while that thread is held.
   Its time stamps are sim time, which stands still during a load.
5. **The test's watching thread only saw clearly during holds.** XPPython3 keeps Python's lock
   while X-Plane runs, so outside the holds a look at the open files took seconds instead of
   milliseconds. A native plugin, or a watcher in another process, does not have this problem.

Not tested yet: starting a new flight from within a flight (a teleport).

## Green that never goes away (same session)

After the start, Shift+4 (circle view) zoomed out to 5 km and then 22.6 km from the aircraft, towards
Istres. X-Plane loaded sharper levels around the camera straight away. The streamer sees the camera
every 2 s and then needs 1-3 s to build, so X-Plane read the neutral levels first. X-Plane does not
read again a level it already has, so the green stayed.

The files themselves were right afterwards:
- every texture within 40 km of LFML had real data at the level X-Plane reads there;
- level 4 was real everywhere within 260 km.

Without FUSE, a camera that moves faster than the streamer builds finds whatever the files hold
at that moment.

## Flight changes (same evening, with the real plugin)

A new flight started from within a flight, with the same aircraft, loads no aircraft. X-Plane then
sends none of PLANE_LOADED, FMOD_BANK_LOADED and LIVERY_LOADED, so X-Plane did not wait at LSGG.
The streamer reloaded the scenery a minute later.

With every message noted in Log.txt, the order around each start is:

```
I/FCG: Applying changes from Flight Configuration screen
I/FCG: Starting new flight in Aircraft/ToLissA320_V1p1p5/a320.acf at Runway 14L
OrthoStudio XP Live: message 115 (1)
OrthoStudio XP Live: X-Plane waits at the start of a new flight until the ortho around 43.6265 1.3689 is built (3247 textures to go)
OrthoStudio XP Live: the ortho is built after 58 s: X-Plane goes on
I/FLT: Init dat_p0 type:'runway_start' apt:LFBO rwy:14L ...
(the DSF loads)
OrthoStudio XP Live: message 104 (0)      SCENERY_LOADED
OrthoStudio XP Live: message 103 (0)      AIRPORT_LOADED
```

- **Message 115 (1)** is undocumented. X-Plane sends it right after "Starting new flight", before
  it loads the aircraft (at a first flight) or the scenery. The position datarefs already give the
  new start: 43.6265 1.3689 is Toulouse-Blagnac.
- Held there:
  - LFBO waited 58 s for 3,247 textures, with no reload afterwards;
  - LGAV (Athens) waited 37 s for 1,077 textures, with no reload afterwards.
- At a first flight, 115 (1) comes before PLANE_UNLOADED (106), the aircraft's loading,
  FMOD_BANK_LOADED (112, 0 then 1) and LIVERY_LOADED (108). The plugin holds there only once a
  flight has loaded, so that the first flight's aircraft loads while the streamer builds.
