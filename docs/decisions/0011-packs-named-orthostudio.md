# 0011. The packs OrthoStudio XP builds carry its name

Date: 2026-09-14. Status: accepted. Supersedes the last point of decisions 0009 and 0010 (the pack
folder names `zOrtho4XP_<tile>` and `yOrtho4XP_Overlays`, and the `Ortho4XP_<tile>.cfg` of a
pack, stay).

## Context
A tile OrthoStudio XP built was a `zOrtho4XP_<tile>` folder holding `Ortho4XP_<tile>.cfg`, its
overlays in `yOrtho4XP_Overlays`: Ortho4XP's layout, kept so that a rebuilt tile took the place of
the Ortho4XP tile of its square and Ortho4XP's window could reread its settings. Once nothing
depended on Ortho4XP (0010), those names were the last trace of it in what OrthoStudio XP writes,
and they had a cost:
- a folder in Custom Scenery did not say who built it: the Library, the uninstall and the delete
  told an Ortho4XP tile from an OrthoStudio XP tile of the same name by its path and its
  `orthostudio.toml`;
- an OrthoStudio XP tile could not be installed while Custom Scenery linked Ortho4XP's overlay
  folder: the two overlay packs had the same name, and the install met `XP_PACK_CONFLICT`;
- when that link led to Ortho4XP's folder, deleting an OrthoStudio XP tile once moved Ortho4XP's
  overlay DSF of the tile into the deleted pack (review of the delete, v1).

## Decision
- OrthoStudio XP names its packs `zOrthoStudio_<tile>` and `yOrthoStudio_Overlays`
  (`orthostudio.model.PACK_PREFIX`, `OVERLAY_PACK`), and the settings file of a pack
  `tile_settings.cfg`.
- The tiles imported from an Ortho4XP folder keep Ortho4XP's names, `zOrtho4XP_<tile>` and
  `yOrtho4XP_Overlays` (`install.scenery_packs.IMPORTED_PACK_PREFIX`, `IMPORTED_OVERLAY_PACK`).
  `scenery_packs.ini` orders them like OrthoStudio XP's own packs, and the Library, `osxp install`,
  `osxp uninstall` and the API take both names (`install.library.pack_tile`).
- A tile OrthoStudio XP installs goes right above the imported tile of its square when that one is
  listed: X-Plane draws the first base mesh it finds for a square.
- `tile.pack` and `tile.install` move to version 2: the next build of a stored tile writes and
  installs its pack under the new names, from the cache.
- No migration code: the packs built under the old names, on the one machine that had some, were
  renamed and linked again by hand.

## Consequences
- An OrthoStudio XP tile and an Ortho4XP tile of the same square can both be in X-Plane. X-Plane
  shows the photos of OrthoStudio XP's, listed first, but draws the overlays of both, so the roads
  and objects of that square appear twice until one of the two overlay DSFs leaves X-Plane
  (`docs/how-it-works.md`, section 3).
- Ortho4XP's window can no longer reread the settings of an OrthoStudio XP tile; the pack keeps
  them in `tile_settings.cfg` and `orthostudio.toml`.
- A pack built before this decision (a `zOrtho4XP_<tile>` folder holding `orthostudio.toml`) is
  still recognised as OrthoStudio XP's by the uninstall and the delete, but its overlay stays in
  the old `yOrtho4XP_Overlays` pack, which they no longer manage, and a new build of its tile
  installs a second pack instead of replacing it.
