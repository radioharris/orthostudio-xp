# The X-Plane plugin of OrthoStudio XP Live

`live_gate.c` and `live_panel.m`: while X-Plane loads a flight, the plugin holds it until the
streamer has built the ortho around the start, and shows its own loading window meanwhile
(`docs/specs/live.md`). macOS only for now: the window is Cocoa, and the pack relies on APFS.

`orthostudio/live/plugin.py` builds it with clang against the XPLM framework of the X-Plane it goes
into, into `build/OrthoStudio XP Live.xpl` (arm64 and x86_64 in one file). It declares the few XPLM
functions it uses itself, so the X-Plane SDK is not needed. An installer will ship it prebuilt in
`orthostudio/bin`, as it does Triangle4XP and DSFTool.
