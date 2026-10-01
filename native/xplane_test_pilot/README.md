# The test pilot of OrthoStudio XP Live's test flights

`test_pilot.c`: an X-Plane plugin that flies the replayable test flights
(`docs/specs/live/flights.md`): it places the aircraft at a scenario's start, moves it along the
scenario's path at every frame, points the camera, takes screenshots and records every frame. It
does nothing without a scenario in `Output/osxp_test_pilot/`, and is not part of what Live installs
for its users.

On a Mac, `tools/live/flights/fly.py` builds it with clang against the XPLM framework of the
X-Plane it goes into, into `build/OrthoStudio XP Test Pilot/mac_x64/` (arm64 and x86_64 in one
file), and installs it in X-Plane's `Resources/plugins`. For Windows and Linux it is built ahead,
into `prebuilt/win_x64` and `prebuilt/lin_x64`, by `tools/live/plugin_builds.py` (Docker: MinGW-w64
against `XPLM_64.def`, gcc for Linux), `prebuilt/SOURCES` holding the digest of what they were built
from, which `tests/test_live_pilot.py` checks. It declares the few XPLM functions it uses itself,
as `../xplane_plugin/live_gate.c` does: the X-Plane SDK is not needed.
