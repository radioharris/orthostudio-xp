# 0013. A window of its own, drawn by the system's web view

Date: 2026-09-20. Status: accepted.

## Context
The interface is a page, and until now the page was a tab in the user's browser. A tab is not a
window, and every consequence was a report of its own:

- the app's icon left the Dock while it ran, because the process never opened a window;
- a click on that icon had nothing to bring in front: it bounced and did nothing (0.1.8 worked
  around it by ending the launcher, which left macOS with no app to activate);
- every click opened one more tab, and the tabs piled up;
- two tabs of the same app do not follow each other: change a setting in one and the other keeps
  the old one, and a build started from the stale tab carries its own provider and zoom level
  while the engine reads the rest from the file.

Measured before deciding: the page and its MapLibre GL map run in WKWebView at WebGL 2.0 on the
Apple GPU, with no error; the window and its page cost about 220 MB, which is what the browser tab
cost; pywebview and pyobjc add about 18 MB to a 267 MB app. The alternatives were Electron (about
150 MB of Chromium, a second language and toolchain, and a new Electron major every 8 weeks of
which only the last three are supported) and a native Python interface (which would throw away the
11 709 lines of the interface and has no answer at all for the map).

## Decision
- The app shows its page in a window of its own, through the web view the system already carries:
  WKWebView on macOS, WebView2 on Windows, WebKitGTK on Linux (`orthostudio/window.py`, pywebview).
  Not Electron, not Tauri, not a native interface: no Chromium to ship, no second language, and
  the page, the API and the engine are untouched.
- The window is held by the app's own process, and the engine is the one put aside, in a process
  of its own. macOS knows a window by the bundle its process came from, and a window opened by a
  process started aside is called Python and carries Python's icon (measured). The 0.1.8 launcher,
  which ended so that macOS held no windowless app, therefore goes back to a plain `exec`.
- The engine starts only once the window is on screen. A system with no web view is left exactly
  as it was found, and the browser opens instead, which is what every version until 0.1.9 did.
- **The close button puts the window away; it does not quit the app.** That is what closing a
  window means on macOS: the app stays, active, its name in the menu bar, its icon in the Dock,
  and a click on that icon brings the window back. Only the window is put away. Hiding the whole
  application was tried first, because pywebview has no `applicationShouldHandleReopen` and a
  window put away would have stayed away; it hands the menu bar to whichever app comes next, which
  a user saw at once. OrthoStudio XP answers that message itself, in the delegate it already holds
  for Quit. A *Window* menu, which pywebview builds for no one, holds the other way back, with
  Cmd+0 put on it by hand since a pywebview menu entry carries no key. Windows and Linux have no
  such place to stay in, and close as they always did. **Nothing else closes the app.** An automatic quit, after some while
  put away, was written and taken out again: an app that vanishes from the Dock on its own is an
  app that went away without telling anyone, and deciding that is the user's, not ours. The window
  still closes when *Quit* stops the engine, because the user asked for that.
- **Quitting the app asks the page.** Cmd+Q, the Quit of the app's own menu and the Quit of its
  Dock menu all end in `applicationShouldTerminate:`, which pywebview answers by asking each
  window whether it may close: with a close button that puts the app away, the app could not be
  quit at all. OrthoStudio XP takes that answer back with a delegate of its own, brings the window
  in front whether it was put away or not, and presses the page's own Quit button. That question
  is already written, already translated, and already counts a build that runs and the ones that
  wait; asking it again in a second voice would be a second thing to keep in step. Force Quit
  cannot be answered by anyone: the engine, left behind, stops by itself once its build is done,
  as it does for a browser whose tab was closed.
- **OrthoStudio XP installs nothing on a system it does not own.** Where a library is missing, the
  doctor's `window` check names it with a link, and the page lists it at the foot. The one
  exception is the Windows installer, which *offers* the WebView2 Runtime, ticked and refusable,
  and only on the machines whose registry says it is missing.
- macOS carries the web view and the app carries the rest, so a macOS build refuses an app whose
  `window` check is not `ok`: a packaging mistake fails the build rather than becoming a runtime
  workaround.

## Consequences
- On macOS and Windows the app is an app: one window, an icon that stays, a click that brings it
  back. The tabs, and everything that followed from them, are gone.
- Linux keeps today's behaviour until the user installs the packages the check names. Windows and
  Linux are untested by their author, who has only a Mac: the browser fallback is what makes that
  acceptable, and a Windows pass is owed before the release.
- pywebview is asked for on macOS and Windows only (`pyproject.toml`), so nothing new is required
  to run the engine, the tests or CI on Linux.
- `possible()` answers without importing the web view or the toolkit under it. Importing the
  toolkit is what opens a connection to the window server, and the process then grows an icon in
  the Dock: the engine asks this for the doctor's check, and a second icon beside the window's
  would be the very thing this decision set out to remove. What it cannot see from there, on Linux
  a missing WebKit2 typelib, makes `show()` raise before anything is started, and the browser
  opens.
- A checkout's app (`tools/package/checkout_app.py`) shows in the Dock as Python when the venv's
  Python is a framework build, Homebrew's among them, because the window then takes the identity
  of the framework's own `Python.app`. It is a developer tool; the installed app, which carries a
  standalone Python, is right.
