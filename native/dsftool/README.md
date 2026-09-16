# DSFTool

DSFTool converts X-Plane DSF files to text and back. OrthoStudio XP runs it to extract the overlays
(roads, railways, power lines, forests, buildings) of a tile from X-Plane 12's Global Scenery into
the shared `yOrthoStudio_Overlays` pack (`src/orthostudio/overlays/`, `docs/specs/overlays.md`).
`src/orthostudio/overlays/dsftool.py` (`find_dsftool`) takes the binary of this folder for the
running platform.

| Platform | File | SHA-256 |
|---|---|---|
| macOS (universal: arm64 and x86_64) | `mac/DSFTool` | `4d90f128637c68972297b8313da54a7b5b5450b773ef78810c61e3aacb713bb1` |
| Windows (x86-64) | `win/DSFTool.exe` | `5ffa7d2bdc5f1d5bb84e27e7d11addf663785f0d10d10023c995d4004cddac41` |
| Linux (x86-64) | `lin/DSFTool` | `8de2fd5ac5aef4da45bec1be71729d77ceb1ae7e27b4290fbe92bf70a320baf6` |

Version: `DSFTool 2.3.0-b2, Copyright 2023 Laminar Research. Compiled on Apr 8 2023. Part of
X-Plane Scenery Tools release: 23-4` (`DSFTool --version`). The three binaries are the ones
Ortho4XP redistributes in its `Utils/` folder (commit `26ec00a`), copied unchanged.

## Licence

DSFTool is part of the X-Plane Scenery Tools by Laminar Research. As Ortho4XP's notice for it
states: the scenery tools are open source; code original to Laminar Research is available under
the MIT/X11 licence, and the libraries they require have licences compatible with either MIT/X11
or the GPL. Their source code is published by Laminar Research as the X-Plane Scenery Tools
(`xptools`).
