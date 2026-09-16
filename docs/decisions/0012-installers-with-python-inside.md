# 0012. Installers carry their own Python, unsigned for now

Date: 2026-09-14. Status: accepted.

## Context
OrthoStudio XP ran from a checkout: `uv`, a terminal, Python 3.12 or later, and Triangle4XP built
by hand. `tools/launcher/make_macos_app.py` only wrapped a checkout's `.venv` in a macOS app. A
pilot who does not program could not install it, and nobody could run it on Windows or Linux
without the development tools.

## Decision
- One installer per system, each holding a standalone CPython (python-build-standalone, the one uv
  installs), the dependencies at the versions of `uv.lock` and OrthoStudio XP itself, installed
  with `uv sync --frozen --no-dev --extra server --no-editable` into that CPython: the same wheels
  the tests run with, and no second list of dependencies to keep in step. A macOS `.dmg` (Apple
  Silicon), a Windows setup program built with Inno Setup (for the current user, no administrator)
  and a Linux `.tar.gz` (`docs/specs/packaging.md`). Not PyInstaller, py2app or Briefcase: each
  would bring its own way of finding modules and data, which the package already finds by itself
  from its folder.
- Triangle4XP and DSFTool go into `orthostudio/bin`; `orthostudio.programs` finds them there
  before the PATH and the checkout.
- The app starts `python -m orthostudio.desktop`: `osxp serve --open` with its output in the
  platform's log folder.
- `tools/package/build.py` builds the installer of the system it runs on; the release workflow
  builds and checks the three on GitHub's machines, and a tag `v*` publishes them.
- Not signed, at the user's choice: Developer ID with notarisation (macOS) and a code-signing
  certificate (Windows) need paid accounts in the publisher's name. The README tells how to open
  the app the first time.

## Consequences
- A user installs OrthoStudio XP without Python, `uv` or a compiler. The data stay in
  `~/.orthostudio`, whatever is installed or removed.
- macOS and Windows warn at the first launch until the installers are signed.
- The macOS app runs on macOS 14 or later, Apple Silicon only: the wheels of the build machine
  decide, numpy and scipy asking for 14.0.
- The installers weigh 68 MB (Windows) to 146 MB (Linux), the macOS app 266 MB once installed:
  CPython, numpy, scipy (about 100 MB installed), pyproj, shapely, Pillow, and the compiled `.pyc`.
- A new version is a new installer; there is no automatic update.
- `tools/launcher` becomes `tools/package`: `checkout_app.py` keeps the app that runs a checkout.
