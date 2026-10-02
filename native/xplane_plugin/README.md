# The X-Plane plugin of OrthoStudio XP Live

`live_gate.c`: while X-Plane loads a flight, the plugin holds it until the streamer has built the
ortho around the start, and shows its own loading window meanwhile (`docs/specs/live.md`). The
window is drawn by `live_panel.m` on a Mac (Cocoa), `live_panel_win.c` on Windows (Win32) and
`live_panel_x11.c` on Linux (X11, opened only when it shows).

On a Mac, `orthostudio/live/plugin.py` builds it with clang into `build/OrthoStudio XP Live/mac_x64`
(arm64 and x86_64 in one file: Apple Silicon and Intel Macs), linked to a stub of X-Plane's XPLM
framework: neither X-Plane nor its SDK is needed. The Windows and Linux plugins are built ahead,
with Docker:

```bash
uv run python tools/live/plugin_builds.py
```

into `prebuilt/win_x64` and `prebuilt/lin_x64`, with `prebuilt/SOURCES`, the digest of the sources
they come from: `tests/test_live_gate.py` fails until they are built again after a change. The
plugin declares the few XPLM functions it uses itself, so the X-Plane SDK is not needed;
`XPLM_64.def` lists them, for the Windows import library and the Mac's stub.

The installers ship it in `orthostudio/bin`, as they do Triangle4XP and DSFTool
(`tools/package/build.py`): the Mac's built with the installer, Windows' and Linux's copied from
`prebuilt`, which the build refuses if their sources changed since.
