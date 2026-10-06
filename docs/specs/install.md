# Install: X-Plane detection, `scenery_packs.ini`, pack links, library, import of Ortho4XP tiles

Status: P2, written before the code of `src/orthostudio/install/`. Tests: `tests/test_install_*.py`
(unit tests on synthetic trees; `xplane`-marked tests read, never write, the local X-Plane 12
install). Origin: Ortho4XP `O4_GUI_Utils.py` (tile collection window) and `O4_File_Names.py`;
`scenery_packs.ini` handling is **new** (Ortho4XP never touches that file; the user edits it by
hand).

## 1. Scope

One package, four responsibilities, all stdlib:

| Module | Responsibility |
|---|---|
| `xplane.py` | find the X-Plane 12 folder on the three OSes; tell whether X-Plane is running |
| `scenery_packs.py` | read, reorder and rewrite `Custom Scenery/scenery_packs.ini` |
| `packs.py` | link (or copy) a built pack into `Custom Scenery`, and undo it |
| `library.py` | the sqlite library of built tiles (`~/.orthostudio/library.sqlite`) and `import-ortho4xp` |

Nothing here ever writes into an X-Plane installation other than through `SceneryPacks.save` and
`install_pack`/`uninstall_pack`, which the caller points at an explicit `Custom Scenery` directory
(tests use copies in a temporary directory).

`TileRef` (`lat`, `lon`, `.name` = `+43+005`, `.folder` = `+40+000`) is `orthostudio.model`'s.

## 2. X-Plane detection (`xplane.py`)

Rule. X-Plane 12's installer records every installation in a text file, one absolute
directory per line, trailing `/` included (verified on the reference Mac:
`~/X-Plane 12/` then `/Applications/X-Plane 12/`, the second of which no longer
exists):

| OS | Install file |
|---|---|
| macOS | `~/Library/Preferences/x-plane_install_12.txt` |
| Windows | `%LOCALAPPDATA%\x-plane_install_12.txt` |
| Linux | `~/.x-plane/x-plane_install_12.txt` |

`detect_xplane()` returns the first line of that file that is a valid X-Plane folder, else the
first valid folder among the usual candidates (`~/X-Plane 12`, `/Applications/X-Plane 12`
on macOS; `C:\X-Plane 12`, `%USERPROFILE%\Desktop\X-Plane 12`, `%USERPROFILE%\X-Plane 12`
on Windows; `~/X-Plane 12` on Linux), else `None`. `$OSXP_XPLANE_DIR`, when set, is tried
first (the Settings override). A folder is valid when it holds both `Resources/` and
`Custom Scenery/` (`is_xplane_dir`). Keyword-only overrides (`install_file`, `candidates`,
`env`) exist for tests; the public call takes no argument, as the P2a contract says.
`xplane_candidates()` gives the folders tried, in that order: the doctor's `xplane` check reads
them too, and lists them when none is an X-Plane folder (it had its own list, without the
installer's file, and could miss on Windows the X-Plane every command found).

`global_scenery_dir(xp)` is `<xp>/Global Scenery/X-Plane 12 Global Scenery` and
`custom_scenery_dir(xp)` is `<xp>/Custom Scenery`; both only compute paths.

`xplane_running()`. `psutil` is not a dependency. The process list is read with:
Linux, `/proc/<pid>/comm` (no subprocess); macOS and other POSIX, `ps -axo comm=`; Windows,
`tasklist /FO CSV /NH`. The base name of each executable is compared with the known X-Plane
executables `X-Plane` (macOS bundle), `X-Plane-x86_64` (Linux), `X-Plane.exe` (Windows). A
full-command-line match (`pgrep -f`) is deliberately not used: it would match `osxp install
--xplane "/Users/x/X-Plane 12"` itself. A failure to list processes returns `False`
(installing is then allowed; X-Plane only reads `scenery_packs.ini` at start-up).

Acceptance (unit): a fake install file whose first line points at a deleted folder and whose
second line is valid returns the second; no file and no candidate returns `None`;
`is_xplane_dir` rejects a folder without `Resources`. Acceptance (`xplane`, this machine):
`detect_xplane()` returns `~/X-Plane 12`, and `xplane_running()` is a `bool`.

## 3. `scenery_packs.ini` (`scenery_packs.py`)

### 3.1 Format (observed on the reference machine, X-Plane 12.1)

```
I\n
1000 Version\n
SCENERY\n
\n
SCENERY_PACK Custom Scenery/Aerosoft - LFMN Nice Cote d Azur X/\n
SCENERY_PACK *GLOBAL_AIRPORTS*\n
SCENERY_PACK_DISABLED Custom Scenery/z_autoortho/\n
SCENERY_PACK /absolute/path/elsewhere/\n
```

- Header: three lines `I`, `1000 Version`, `SCENERY`, then usually one blank line. Windows
  installs write `\r\n`. The line ending of the file is **detected** (`\r\n` if present
  anywhere, else `\n`) and **preserved** on save; a new file uses `\n` on POSIX and `\r\n` on
  Windows.
- Body: `SCENERY_PACK <path>/` or `SCENERY_PACK_DISABLED <path>/`; the path is relative to the
  X-Plane folder (`Custom Scenery/<name>/`) or absolute, always with a trailing `/`;
  `*GLOBAL_AIRPORTS*` is a virtual pack. Any other line (blank, unknown keyword, comment) is
  kept verbatim in place. Text is decoded as UTF-8 with `surrogateescape` so that undecodable
  bytes survive a round-trip untouched.
- The **name** of a pack is the last path component (`z_autoortho` for
  `Custom Scenery/z_autoortho/`, `foo` for `/abs/foo/`). Matching is by name: X-Plane
  itself identifies packs by folder.

### 3.2 Ordering rule (new)

Target order, top = highest priority for X-Plane: custom airports and everything the user placed
above `*GLOBAL_AIRPORTS*` > `*GLOBAL_AIRPORTS*` > the overlay packs (`yOrthoStudio_Overlays`, its
numbered links `yOrthoStudio_Overlays_2`... (4.4), an imported `yOrtho4XP_Overlays`) > the tile packs (`zOrthoStudio_*`, imported `zOrtho4XP_*`) >
`z_autoortho` (and `z_ao_*`) > the rest. The packs of the tiles imported from Ortho4XP are ordered
like OrthoStudio XP's own (decision 0011).

`ensure(name, kind=...)` is the only mutator and follows these rules, in this order:

1. **Existing packs never move.** If `name` is already listed (enabled or disabled) its line stays
   where it is; a `SCENERY_PACK_DISABLED` line becomes `SCENERY_PACK` (installing means activating),
   except with `reenable=False`, which an overlay pack always gets (`install_receipt`, and
   `install_pack` for that name): a user of simHeaven X-World disables it, since X-World brings the
   same roads and objects, and installing a tile must not turn it back on (user report, 2026-09-13).
   Returns `True` only when something changed.
2. **Nothing above `*GLOBAL_AIRPORTS*` is touched**: the insertion index is never lower than
   the line after `*GLOBAL_AIRPORTS*`. Without that line, the top of the body is the floor
   (ordering then cannot protect airports; the user keeps control).
3. `kind="ortho"` (a tile pack): with `above`, right above that line when it is listed, never above
   the floor (`install_receipt` passes the imported `zOrtho4XP_<tile>` of the same square: X-Plane
   draws the first base mesh of a square, and the tile OrthoStudio XP built is the one wanted).
   Otherwise right after the **last** existing tile pack line; else right before the first
   `z_autoortho`/`z_ao_*` line; else right before the first **base-mesh pack** at or below the floor
   (`SceneryPackEntry.is_mesh`: a name starting with `zzz_`, `XPME_` or `zz_`, or any name other
   than a tile pack sorting after `zOrtho4XP_~`, i.e. the `z*` names X-Plane loads last:
   `zzz_hd_global_scenery4`, `z_...`); else at the end of the body. Without this rule (P2a review)
   an ortho tile of a user with no AutoOrtho line landed *below* their HD mesh, which X-Plane then
   drew instead of the tile. Library packs (`simHeaven_*`, `openSAM_*`) are not mesh packs and stay
   above.
4. `kind="overlay"` (an overlay pack): inserted right before the **first** tile pack line; else
   as in rule 3.
5. The line written is `SCENERY_PACK Custom Scenery/<name>/` (relative form, as X-Plane
   writes for packs inside `Custom Scenery`).

`remove(name)` deletes the line (used by `uninstall_pack`); `disable(name)` turns it into
`SCENERY_PACK_DISABLED` without moving it.

### 3.3 Writing

`save(path, backup=True)`: when `backup` and `path` exists, its bytes are copied first to
`<path>.bak` **only if that file does not exist yet** (the list before OrthoStudio XP first changed
it, kept for good; it used to be overwritten each time) and to `<path>.osxp-previous` (the list
before this save, overwritten each time). Then the new content is written with
`orthostudio.fsutil.atomic_write_bytes` (temporary sibling + `os.replace`). Any `OSError` becomes
`XP_SCENERY_PACKS_UNWRITABLE` with the reason. `load(path)` on a missing file yields an empty list
with the default header (X-Plane rebuilds the list at start-up and appends the packs it discovers;
the file we write is valid).

Acceptance (unit): a synthetic file with `\r\n`, a disabled line, an absolute path, a comment and no
trailing newline round-trips byte for byte through `load`/`save` with no mutation; `ensure`
implements rules 1-5 on a synthetic list covering every branch. Acceptance (`xplane`, this machine,
read-only): the real `~/X-Plane 12/Custom Scenery/scenery_packs.ini` is copied to a temporary
directory, its sha256 is taken before and after the test, `load`/`save` of the copy reproduces the
original bytes, and `ensure("zOrthoStudio_+43+005", kind="ortho")` then
`ensure("yOrthoStudio_Overlays", kind="overlay")` place the two lines between
`Custom Scenery/yAutoOrtho_Overlays/` and `Custom Scenery/z_ao_eur/`, i.e. just before the first
`z_ao_*`/`z_autoortho` line, overlay first, everything above `*GLOBAL_AIRPORTS*` untouched (the
25 lines before it are byte-identical). `yAutoOrtho_Overlays` is neither an ortho pack nor an
AutoOrtho mesh prefix, so it stays above the OrthoStudio XP packs; the relative order of the two
overlay packs is the user's business.

## 4. Pack installation (`packs.py`)

Origin: Ortho4XP `O4_GUI_Utils.py:1713-1780` (`toggle_to_custom`): a symbolic link in
`<custom_scenery>` to the build directory, of the same name, identity checked with
`os.path.samefile(realpath(link), realpath(target))`, removal with `os.remove`.

`install_pack(pack_dir, custom_scenery, *, link=True, update_ini=True) -> Path`:

1. `XP_RUNNING` if `xplane_running()`.
2. Target is `<custom_scenery>/<pack_dir.name>`. A **dangling** link there (the pack was
   moved) is removed first: replacing it destroys nothing the user created. If the target
   exists and is the same directory as `pack_dir` (a link to it, or the pack was built in
   place), nothing is created (idempotent). If it exists and is anything else (a real
   folder, a link to another folder), `XP_PACK_CONFLICT` (raised as blocking: OrthoStudio XP never
   deletes a folder it did not create).
3. `link=True`: `os.symlink(pack_dir, target, target_is_directory=True)`. On Windows, when
   the symlink is refused (no Developer Mode: WinError 1314, most Windows accounts), fall back
   to a junction, made by CPython's `_winapi.CreateJunction` (no program started), else by
   `cmd /d /c mklink /J`. What decides is the disk afterwards (a link at the target that leads
   to `pack_dir`), never cmd's exit code: a cmd AutoRun command that leaves an error level makes
   cmd exit with 1 after mklink made the junction (`/d` skips AutoRun). Otherwise
   `XP_LINK_FAILED`, with what was tried and what is at the target, and what the attempt left
   there (a link, an empty folder) is removed. `link=False`: `shutil.copytree` into a temporary
   sibling, then `os.replace` (a reader sees a complete pack or none).

   **RedirectionGuard.** A Windows process under this mitigation follows no junction made by an
   account without administrator rights (`STATUS_UNTRUSTED_MOUNT_POINT`), and the programs it
   starts inherit it. Inno Setup enables it on its setup program since 6.7 (its `RedirectionGuard`
   directive, yes by default): the app the installer's last page opened had it, made each junction
   and could not follow it, and every install of a user's tiles failed (2026-09-15). The installer
   sets `RedirectionGuard=no` (`packaging.md`); the app still checks (`fsutil.redirection_guard`):
   the doctor's `junctions` check fails, and `XP_LINK_FAILED` says to open the app again from the
   Start menu.
4. `update_ini=True`: `SceneryPacks.load(<custom_scenery>/scenery_packs.ini)`,
   `ensure(name, kind=pack_kind(name))` (`overlay` for an overlay pack), `save`
   with backup, only if something changed. `install_receipt` (`pipeline/pack.py`) installs
   the tile pack and the overlays pack with `update_ini=False` and does one load / two
   `ensure` / one save itself, so the `.bak` is the file as it was before OrthoStudio XP touched it.
   A tile built without an overlay (the setting `essential.overlays = "none"`) installs no
   overlay: `write_pack` has already removed its DSF from the shared pack and any copy
   parked in the tile pack, and when no tile's DSF is left in the shared pack, the install
   removes that pack's link (only a link leading to it) and its line, and the library forgets
   the tile's overlay row.

`uninstall_pack(name, custom_scenery, *, update_ini=True, remove_copy=False) -> bool`:
refuses while X-Plane runs; removes a link or a junction; a real directory is removed only
with `remove_copy=True` and only when it looks like a scenery pack (holds `Earth nav data/`),
else `XP_PACK_CONFLICT`; then removes the line from `scenery_packs.ini`. Returns whether
anything was removed.

Acceptance (unit, temporary `Custom Scenery` copy): install creates a link whose `realpath`
is the pack, the ini gains one correctly placed line, a second install is a no-op (same
ini bytes), a foreign folder at the target raises `XP_PACK_CONFLICT`, a fake running
X-Plane raises `XP_RUNNING`, `link=False` copies, uninstall removes the link and the line
but never a real folder unless asked.

### 4.1 Uninstalling a tile (`pipeline/pack.py`, `osxp uninstall`)

`uninstall_receipt(name, custom_scenery, delete_pack=False)` is what the command and the page
call. `uninstall_pack` alone removed the tile's link and line and left its DSF in the shared
`yOrthoStudio_Overlays` pack, so X-Plane kept drawing the tile's roads and objects over the
default scenery, which already has them. Now, in one locked step:

1. the link goes (a real folder only when it holds `orthostudio.toml`); `XP_RUNNING` refuses first;
2. the tile's DSF of the overlay pack is **parked** inside the tile pack
   (`osxp-overlay.dsf.uninstalled`), which X-Plane no longer reads, and `install_receipt` moves it
   back before linking, so an uninstall followed by an install is a round trip. The overlay is
   OrthoStudio XP's to move only when the tile pack is an OrthoStudio XP pack (a link to a folder
   holding `orthostudio.toml`) and a link of Custom Scenery leads to the overlay pack beside it
   (`<out>/yOrthoStudio_Overlays`, where `osxp build` put the DSF and where `install_receipt` puts
   it back: the real paths are compared), under `yOrthoStudio_Overlays` or a numbered name (4.4).
   Otherwise the overlays stay as they are:
   * **for a tile built by Ortho4XP, only the tile's link is taken out, and its overlay stays in
     Ortho4XP's `yOrtho4XP_Overlays` folder** (and in X-Plane, when that folder is linked there).
     Parking it used to move Ortho4XP's DSF into Ortho4XP's tile folder, take Ortho4XP's overlay
     link out, and Install never put either back (review of the delete, v5);
   * an overlay link of that name leading to another folder (the overlay pack of another output
     folder) is not this pack's either: when both overlay packs were named `yOrtho4XP_Overlays`,
     deleting an OrthoStudio XP tile while that link led to Ortho4XP's folder parked Ortho4XP's DSF
     into the OrthoStudio XP pack and deleted it with the pack (v1);
   * a tile pack that is gone (its link leads nowhere) has no `orthostudio.toml` to tell: its DSF
     stays in the overlay pack (moving it into the missing folder raised `FileNotFoundError` until
     the delete of 4.2 was written), and the delete removes it when the library says it is
     OrthoStudio XP's;
3. the overlay pack's link goes when no DSF is left in it, under the same condition; an overlay
   pack that is a real folder (a copy) is never modified. **A tile installed as a copy
   (`osxp install --copy`) has its overlays in a copied overlay pack, a real folder: its DSF stays
   there after an uninstall or a delete**, and so does the overlay pack's line, until the user
   removes the copy by hand (review of the delete, v7);
4. both lines leave `scenery_packs.ini` in one save;
5. `delete_pack` deletes the tile pack directory (`osxp clean` then frees its store space). The
   command no longer uses it: `osxp uninstall --delete` is the delete of 4.2.

The page's Uninstall sends the row's `path` (`api.md` 2.3): when Custom Scenery holds under that
name something else than the row's pack (`is_installed`, the rule of step 3 of 4.2: a link leading
to another pack, another row's pack built straight into Custom Scenery, a copy of another build),
it is 409 `XP_PACK_CONFLICT` and nothing changes.

Acceptance (unit, `tests/test_uninstall.py`): uninstall then install is a round trip; the shared
overlay pack stays for the other tiles; a tile not installed changes nothing; nothing moves while
X-Plane runs; a pack gone does not crash; an Ortho4XP tile leaves X-Plane without its overlays,
whose folder and link stay; an OrthoStudio XP tile whose overlay link leads to another folder parks
nothing.

### 4.2 Deleting a tile (`pipeline/pack.py`, the page's Delete, `osxp uninstall --delete`)

The Library screen could take a tile out of X-Plane but not delete it, and `osxp uninstall --delete`
deleted a pack only while it was installed, left its library rows (the Library kept listing a tile
whose files were gone) and left its space in the store until `osxp clean`.
`delete_receipt(pack_dir, *, tile, custom_scenery=None, library_path=None, store_root=None,
tiles_root=None, grace_s=None)` is what both call; the roots default to the OrthoStudio XP home's,
those of `osxp clean`. In this order, and nothing at all when one of the first two steps refuses:

1. **Refusal**, `SYS_PACK_NOT_OSXP` (409 in the API): OrthoStudio XP deletes only what it built.
   Refused are a tile the library records as built by Ortho4XP, and a `pack_dir` that is a link, a
   file, a folder without `orthostudio.toml` (it may hold the user's own files) or the pack of
   another tile (its `orthostudio.toml` names another tile). The remedy: Uninstall takes the tile
   out of X-Plane without deleting anything; the folder itself is the user's to delete by hand. The
   folder of an OrthoStudio XP row already gone is no refusal: its rows still have to go.
2. **X-Plane running**, `XP_RUNNING` (409), whenever `custom_scenery` is given, the tile
   installed or not: a tile that is not installed may still have its overlay DSF in the overlay
   pack X-Plane reads for another tile. Checked after the refusal, which no quitting would
   change, and before anything is touched. It used to be checked by the uninstall of step 3
   only: the overlay DSF of a pack already gone was deleted before it, and a tile not installed
   had no check at all (review of the delete, v2). Without `custom_scenery` (X-Plane not found)
   there is no check.
3. **Out of X-Plane**, when `custom_scenery` is given and the pack is installed there:
   * a link of the pack's name that leads to `pack_dir`, or to where it was once it is gone (the
     real paths are compared). A link of that name leading anywhere else, broken or not, is
     another pack's and stays: with the pack gone, any broken link used to count (v4);
   * the pack itself, built straight into Custom Scenery;
   * a copy of it (the same `orthostudio.toml`, byte for byte) that is not the folder of another
     library row: the same build made twice, once into the output folder and once straight into
     Custom Scenery, has the same manifest, and deleting the first one used to delete the
     second (v3).

   `uninstall_receipt` (4.1) takes it out: link or copy, the overlay when it is OrthoStudio XP's,
   the `scenery_packs.ini` lines. Anything else of that name in Custom Scenery is the user's and
   stays. Without `custom_scenery` the step is skipped.
4. **The files.** The tile's DSF in the overlay pack beside the pack (`<out>/yOrthoStudio_Overlays`)
   goes when OrthoStudio XP put it there: the pack's manifest lists an overlay or, the pack being
   gone, the library records that overlay pack as OrthoStudio XP's for the tile. Left there, X-Plane
   would draw the deleted tile's roads and objects as soon as another tile links the overlay pack.
   Then the pack directory, `orthostudio.toml` last: a deletion stopped halfway (a file held open,
   on Windows by X-Plane: `SYS_WRITE_FAILED`, rows untouched) leaves a folder that is still
   recognisably OrthoStudio XP's, and the delete can be asked again. With the pack gone, the overlay
   pack's link in Custom Scenery is left as it is (4.1), even when this was its last DSF.
5. **The library** forgets the pack's `ortho` row, the `overlay` row of the overlay pack beside
   it, and every `overlay` row of the tile once no `ortho` row of it is left.
6. **The store** gives back the tile's own cache, and nothing else:
   `orthostudio.clean.clean_after_delete` collects the artefacts the pack's `orthostudio.toml` names
   (read before the pack goes; a pack already gone left the same keys in its library row) and what
   they were built from, except what a remaining pack needs, what is pinned, and what was used in
   the last ten minutes (`DELETE_GRACE_S`: a build running in another process may share it). It used
   to run the whole `osxp clean`: other processes' finished artefacts that no pack referenced yet
   could go, the deleted tile was credited with other tiles' superseded builds, and with the hour of
   grace a tile built less than an hour before freed nothing. Superseded builds and abandoned
   temporary folders stay for `osxp clean`. The tile is gone by then: a clean that fails is no error
   but the receipt's `warning`, a plain sentence saying that `osxp clean` frees the space later.

The receipt: `{format: "osxp-delete-1", name, tile, removed_from_xplane, pack_deleted,
freed_bytes, custom_scenery, warning}`. `pack_deleted` is `false` when the directory was already
gone; `warning` is `null` unless step 6 failed. `freed_bytes` is what the disk got back, inode by
inode: the files of the pack (and of a copy removed from Custom Scenery) that nothing else links
to, measured before they go, plus what the store clean freed (nothing when it failed). A DDS
hard-linked from the store frees nothing when the pack goes and its full size when the clean
removes its last artefact, so nothing is counted twice.

Which pack: the API takes the library row of `{name}` at `path` (`api.md` 2.3).
`osxp uninstall <tile> --delete` takes the library's newest OrthoStudio XP row of the tile, else
its newest row (then refused if it is Ortho4XP's); a tile the library does
not know, built without `--install`, is looked for where its link in Custom Scenery leads, then in
`<data folder>/tiles`. X-Plane is optional there too (`--xplane`, then detection). The command prints
what happened in plain words: taken out of X-Plane, the folder deleted (or already gone), no longer
in the library, how much disk space was freed, and the warning if any.

Acceptance (unit, `tests/test_delete.py`, a temporary home, store and `Custom Scenery`): an
installed tile leaves X-Plane, its folder, the library and the store, and `freed_bytes` equals what
`disk_bytes` measured of the pack, its overlay DSF and its artefacts; a second tile keeps the
overlay pack, its rows and its cache; a tile not installed, a tile never installed (no row, no
X-Plane) and a folder deleted by hand are deleted all the same; the store gives back the deleted
tile's cache only (a shared artefact, a superseded build and an artefact used in the last ten
minutes stay) and a clean that fails is a warning; an Ortho4XP row, a folder without
`orthostudio.toml`, a link and the pack of another tile are refused with every file and row
unchanged; Ortho4XP's overlays, another row's pack built in Custom Scenery and a link to another
pack stay; a copy in Custom Scenery leaves with the tile, its copied overlay DSF stays, a copy of
another build stays; while X-Plane runs nothing changes, for a tile installed, one whose folder is
gone and one not installed; a deletion stopped halfway can be asked again; the command deletes an
installed tile and an unknown one from the output folder.

### 4.3 The overlays of other packs (`pipeline/pack.py`)

A user asked what happens with AutoOrtho or XPME and OrthoStudio XP together (2026-09-14). For
each square X-Plane draws the base mesh of the first pack that has it, the OrthoStudio XP tile
since it is listed above `z_autoortho`, `z_ao_*` and the base meshes (3.2), but it draws the
overlays of **every** active pack that holds the square. AutoOrtho's `yAutoOrtho_Overlays`, XPME's
`XPME_Overlays` and Ortho4XP's `yOrtho4XP_Overlays` hold the squares of their regions, so on a
square that also has an OrthoStudio XP tile the roads, forests and buildings came twice: roads that
flicker, trees and buildings doubled. On the user's machine `yAutoOrtho_Overlays` held
`+46+006` and `+46+007`, the squares of their first tiles (AutoOrtho was disabled then).

`overlay_states(custom_scenery)` reads, and changes nothing. For each OrthoStudio XP tile X-Plane
shows (its `zOrthoStudio_` line active, its link leading to a pack that holds `orthostudio.toml`),
the other overlay packs are the active lines, other than `yOrthoStudio_Overlays`, whose name ends
with `Overlays` and whose folder holds the square's DSF (a line's folder is `Custom Scenery/<name>`
under the X-Plane folder, or its absolute path; a link to a folder that is not there, an XPME disk
not mounted, holds nothing). The state is `own` (the tile's overlay alone), `double` (the tile's and
another's), `left` (another's alone, as the user chose) or `missing` (left to packs no longer
active: none drawn).

`leave_overlay(pack_dir, tile)` leaves the square to the other packs: the tile's DSF of
`yOrthoStudio_Overlays` moves into its pack as `osxp-overlay.dsf.left`, which X-Plane does not read.
Nothing of the other tool's is touched. The choice stays:

* through an uninstall and an install: `uninstall_receipt` finds no DSF of the tile in the overlay
  pack to park, and `install_receipt` puts back only the parked one (`osxp-overlay.dsf.uninstalled`);
* through a build again: `write_pack` places the new overlay DSF as `osxp-overlay.dsf.left` when
  that file is there, and removes the one of the shared pack; `pack_is_intact` counts it, so a
  build does not assemble the pack again for it;
* a build without overlay (`essential.overlays = "none"`) removes it with the others; a delete
  removes it with the pack.

`take_back_overlay(pack_dir, custom_scenery, tile=...)` moves the DSF back into the overlay pack,
then installs as `install_receipt` does (the overlay pack's link and line). Both refuse with
`XP_RUNNING` while X-Plane runs, since it reads the overlays when it starts; the API refuses a tile
in a build under way or waiting (`SYS_TILE_IN_BUILD`).

Acceptance (`tests/test_overlays_of_other_packs.py`, a fake X-Plane): `own`, then `double` once
AutoOrtho's overlay of the square is listed active; `left` after leaving it, through an uninstall,
an install and a build again; `missing` once AutoOrtho's line is disabled; `own` after taking it
back, the overlay pack's line there; XPME's and Ortho4XP's overlay packs count only while active,
a simHeaven pack never; `GET /api/library` gives the state of the row X-Plane shows, and
`POST /api/library/overlays` changes it.

### 4.4 The tiles of several folders (`pipeline/pack.py`)

The tiles folder is `<data folder>/tiles`, and the data folder can change in Settings
(`essential.data_dir`, 2026-09-15; `pipeline-textures.md` 2): nothing is moved, and the tiles built
before stay installed from the old folder while the next ones install from the new one. Each
tiles folder has its overlays pack beside its tiles, and X-Plane shows both:

* `overlay_link(custom_scenery, overlay_pack)` is the link of Custom Scenery that leads to a
  folder's overlays pack, among OrthoStudio XP's overlays names (`OVERLAY_LINK_NAME`:
  `yOrthoStudio_Overlays`, `yOrthoStudio_Overlays_2`, `_3`...), or `None`. `install_receipt`
  installs the overlays pack under that link, else under the first free name: absent, or a broken
  link into a folder no tile link of Custom Scenery leads into (deleted by hand; while tile links
  lead there, its disk may only be unplugged and the name waits for it). A copy (`link=False`) keeps
  the pack's own name. A name new to the folder, a copy's too, starts its line as a new one,
  whatever line that name had for a folder before: enabled, or disabled with `overlay_off`, the
  state of the line the tile's roads came from; the roads of a new folder took over a disabled line
  and were not drawn (found on the owner's Mac, 2026-10-06). `uninstall_receipt` parks the tile's
  DSF when its folder's overlays pack has a link, and takes that link and its line out once the pack
  is empty; `overlay_states` and the Library's `installed` read the link the same way. `pack_kind`
  counts the numbered names as overlay packs, so their lines sit with the overlays (3.2).
* A tile built again into another folder takes the place of its old build:
  `install_receipt` first takes the link `zOrthoStudio_<tile>` out when it leads to an OrthoStudio
  XP pack of the same tile elsewhere, as `uninstall_receipt` does (its overlay parked in that pack,
  its line removed), then installs. The old pack stays on the disk and in the library, where it can
  be installed again, which takes the new one out the same way, or deleted. A link to anything
  else keeps `XP_PACK_CONFLICT`.

The first version refused a build that installed while tiles of the old folder were installed,
and asked to delete them in the Library first; the user who asked for the data folder did not
understand why (2026-09-15), and the second link made the refusal useless.

Acceptance (`tests/test_data_folder.py`, a fake X-Plane): a tile of a second folder installs beside
a tile of the first, `yOrthoStudio_Overlays_2` leading to its overlays, both lines above the tiles,
both tiles `own` in `overlay_states`, all four Library rows installed, and its uninstall takes only
its own link out; a tile built again into another folder takes the place of its old build, whose
overlay is parked, and the old build installed again takes it back; an unplugged folder keeps the
first name (the second folder's link is `_2`, both tiles `own` once it is back), and a folder
deleted by hand, its tiles taken out, frees it.

### 4.5 A tile moved by hand (`pipeline/pack.py`, the atelier, step 2)

A user files his tiles by area with the Finder: the Library listed the tile as missing, and X-Plane
kept a link into nothing. `find_again_receipt(entry, chosen, custom_sceneries, library_path)`
finds it again where the user shows it (2026-10-05):

* The folder shown is the tile's own or the one holding it, under its name `zOrthoStudio_<tile>`
  (X-Plane's link and the Library both go by it), holding that very build: the manifest's tile and
  keys are the row's, and what it lists is there, the DSF at its size, as many textures and
  terrain files, the overlay DSF aside (`pack_is_intact(..., with_overlay=False)`). Else
  `SYS_TILE_NOT_IN_FOLDER`, `SYS_TILE_OTHER_BUILD` or `SYS_TILE_INCOMPLETE`, and nothing changes.
  A folder inside an X-Plane's Custom Scenery, put there in place of its link, is refused with
  `SYS_TILE_IN_CUSTOM_SCENERY`: found there it lost its roads and forests, and could no longer be
  taken out. X-Plane running: `XP_RUNNING`, nothing changes.
* Nothing of the tile moves. Its overlay DSF, left in the overlays pack beside the folder it was
  moved from, is parked in it (`_bring_overlay`: copied, then removed, since the two folders may
  be on two disks), as `uninstall_receipt` parks it; a tile taken out of X-Plane later from its new
  place would otherwise have left its roads drawn. A copy made before a stop leaves the one behind
  only until it is asked again, which removes it when it holds the same bytes: X-Plane would draw
  it twice. The same file seen twice, through a link, is the only one there is, and stays.
* Every Custom Scenery given (the X-Plane of Settings and the machine's others) whose link
  `zOrthoStudio_<tile>` leads to the old folder gets the found one through `install_receipt`: the
  broken link replaced, the overlay put beside the tile and its overlays pack linked (4.4), the
  lines kept, a line the user disabled included (`reenable=False`). A new overlays line, for a
  folder X-Plane showed no overlays of, takes the state of the overlays line of the folder the tile
  came from (`overlay_off`, `_overlay_line_off`): a user of simHeaven X-World turns OrthoStudio XP's
  roads off, and they came back on (a review, 2026-10-05). The overlays pack of the folder the tile
  left, out of reach (its disk away, or deleted), leaves that X-Plane, its link and its line, once
  no tile link leads into that folder (`_let_go_of_overlays_out_of_reach`, under the lock of every
  install): the tile's roads stayed there, and the day the disk came back X-Plane drew them twice
  (found on the owner's tiles, 2026-10-06). A tile of that folder still in X-Plane keeps it, its own
  roads being there. A line the user disabled goes as well: kept with its link, X-Plane started
  without the disk dropped the line and gave it back enabled once the disk was back; kept alone, it
  kept the next overlays pack taking that name off only when that name was the first one free (two
  reviews and the owner's Mac, 2026-10-06). Built again, the tile has its roads back under a line of
  its own, enabled. An X-Plane that did not show the tile gets nothing. The Library row follows; the
  overlay row of the folder left goes once it holds nothing of the tile.
* Asked again after a stop half way, it finishes what is left: the X-Planes whose link already
  leads to the found folder are given its install again, which puts the overlay beside it. The
  receipt says when the tile's roads and forests are nowhere (`overlay_lost`): a build gives them
  back.

The Library tells a tile whose disk is away from one whose folder is gone (`home.disk_absent`): on
Windows its drive is not there, elsewhere the first folder above it that is there is where disks
are mounted (`/Volumes`, `/media`, `/run/media`, `/mnt`) or the root. A disk away comes back by
itself once plugged in; a disk renamed, or left for another the tiles were copied to, has *Find
again…* as its way back, and the page offers it there too.

Acceptance (`tests/test_library_find.py`, fake X-Planes): a tile moved alone is found from the
folder holding it or from its own, X-Plane's link and its overlay follow and its images are the same
files; a tile in no X-Plane is found by the Library alone, its overlay parked; every X-Plane that
showed it follows and no other; a wrong folder, a renamed copy, another build and a copy cut short
change nothing; nor does X-Plane running; a tile put in X-Plane's own Custom Scenery is refused and
keeps its roads; the one overlay there is is never removed for itself seen through a link; a line
the user disabled stays disabled, and a new overlays line takes the state of the one the tile came
from, under a name taken over from a folder deleted by hand too, whose old line, on or off, is not
kept; found while the disk of the folder it left is away, that folder's overlays pack leaves
X-Plane, its link and its line, on or off, when no tile of X-Plane is in it any more, and stays
while one is, an install saving meanwhile keeping its own line; built again, the tile's roads are
drawn under a line of their own; a tile in its place, an imported one and one in a build are not
looked for; a tile whose roads went too is found and says so; a stop half way, before or past
X-Plane's link, is finished when asked again, and a copy of the roads made before a stop is not left
twice; a disk away says so, and a folder gone two levels down is not one.

### 4.6 Tiles filed elsewhere (`pipeline/filing.py`, the atelier, step 3)

A user files his tiles by area, on several disks; the page's *File elsewhere…* does it for the
tiles picked (2026-10-05), one at a time, `file_tile(entry, dest, custom_sceneries, ...)`:

* The folder must take a tile (`check_destination`), seen as named and with its links followed:
  not gone (`SYS_FOLDER_GONE`); not inside an X-Plane's Custom Scenery, one named so or the Custom
  Scenery of a known X-Plane however reached, through a link, a junction or another case
  (`SYS_FOLDER_IN_CUSTOM_SCENERY`: a Custom Scenery moved to a big disk and linked back, its real
  folder chosen, made the tile's own link there pass for a copy of it, and the tile was deleted, a
  review, 2026-10-05); not the workshop's `tiles` or another of the data folder's own folders
  (`WORKSHOP_FOLDERS`: `store`, `chunks`, `work`, `mapcache`, `elevation`, `dem`, which the builds
  write and Free space empties whole), nor a folder inside one (`SYS_FOLDER_IS_ATELIER`); not
  inside a tile's folder, which deleting that tile would take away (`SYS_FOLDER_IN_TILE`). X-Plane
  running: `XP_RUNNING`. Nothing changes.
* The tile must be whole where the Library says, its manifest's tile and keys the row's, every
  file it lists there (`pack_is_intact`, its overlay DSF aside): else `not_whole` in the plan and
  `SYS_TILE_NOT_WHOLE`, before anything of it moves. Moved first, it was refused by the switch,
  and left where X-Plane no longer found it (a review, 2026-10-05).
* `filing_plan` says how: `move` when the folder is on the tile's disk (one `os.replace`, at once,
  nothing more on the disk; a rename refused across volumes falls back to the copy); `copy` on
  another disk, with the bytes it writes (every file in full, those shared with the cache too);
  `reuse` when a real folder of the tile's name there, not the tile's own, holds this build whole,
  every file at its size (a copy a stop left in place), then read back the same bytes as the
  tile's before it is taken (`_same_bytes`: a hand copy cut short keeps its sizes); `there` when
  the tile is in that folder already; `taken` when another folder, or a link, of its name is there,
  or a copy that reads back otherwise (`SYS_TILE_NAME_TAKEN`). A link is never a copy: one leading
  to the tile itself passed for it, and the original was deleted.
* A copy needs its bytes and `ROOM_MARGIN` free (`SYS_DISK_FULL`). It is made under
  `<name>.osxp-part` beside where it goes, 8 MB at a time, each file's blake3 digest taken as it is
  read; the files are then sent to the disk as a group, the disk's own cache flushed once
  (`graph.store.send_to_disk`, `flush_disk`: each file forced alone held a hard disk under Windows
  at every one, 2026-09-28), read back against their digests (`SYS_TILE_COPY_DIFFERS`), and the
  folder is renamed into place. A stop (`FilingStoppedError`, asked between two files) or any
  failure takes the temporary folder away; a temporary folder found there is a copy a stop left
  half made, made again. The original is never touched before the copy is in place.
* Then the tile is switched as a tile found again (`find_again_receipt`, 4.5): every X-Plane that
  showed it follows, its roads and forests go into the overlays pack of the folder it goes to, and
  its row follows. A tile copied, the original still there, has every X-Plane that showed it led
  to the copy first (`_lead_to`: its link only, a link that cannot be made giving the old one
  back), so that the switch finds what a move leaves: its line in `scenery_packs.ini` stays as the
  user left it, enabled or not and where he put it (taken out and installed again as a build from
  another folder, 4.4, it came back enabled at its default place, a review, 2026-10-05). It leaves
  the folder it came from last (`_delete_pack_dir`), never when that folder is the copy itself; a
  file another program holds there leaves that folder as it is, its path in the receipt's `left`,
  which the page says.
* Wherever a stop comes, a whole tile is left: the original until the copy is in place, the copy
  after. A stop after a move and before the switch leaves the row's folder gone: *Find again…*
  (4.5) finishes.

The engine files one tile at a time, and nothing else changes a tile meanwhile: the install,
uninstall, overlays, forget, find and delete routes, and a build or its retry, answer 409
`SYS_BUSY` while a tile is filed; a tile is filed only between builds.

Acceptance (`tests/test_library_file.py`, fake X-Planes): a tile moved on one disk keeps its
images' files and is followed by X-Plane, the Library and its roads; a tile copied to another disk
is the same bytes there, read back, and leaves the folder it came from; how far the copy is is
heard, and a stop leaves the tile where it was, the copy made again when asked; a copy that reads
back wrong is taken away; a whole copy a stop left is taken without copying, another folder of its
name is refused; a folder no tile goes into, X-Plane running and a disk without room refuse and
change nothing; the plan says what each tile does and reads only; one filing at a time, nothing
else changing a tile meanwhile. From the review of 2026-10-05: a link of the tile's name, a Custom
Scenery reached through a link or in another case, and a copy cut short at its full size are
refused, the tile where it was; the workshop's own folders are refused; a tile not whole is
refused before anything moves; an old folder a file holds is named in `left`; a Stop asked as the
filing starts is kept; a tile filed to another disk keeps its line as the user left it, and a new
overlays line takes the state of the one the tile came from (4.5).

### 4.7 A tile filed elsewhere, built again (`filing.put_back`, the atelier, step 4)

A tile filed elsewhere and built again (from the Plan, or *Build again* in Works) is built in the
workshop, beside the cache, then its new version is put back in its folder in place of the old
one (2026-10-05): built as any tile, then moved, or copied when its folder is on another disk:

* Its folder (`tile_home`): the one the X-Plane of Settings takes (its link leads there); else the
  workshop when the Library lists the tile there; else the latest of its folders elsewhere. A
  folder gone from a disk that is here does not count: the tile is built in the workshop as a new
  one, and its rows there follow it. A folder on a disk away does: the tile is not built
  (`SYS_TILE_DISK_ABSENT`), and the others of the build are. A folder whose old version a put
  back cut short left under `<name>.osxp-old` has it back under its name first.
* It is never installed from the workshop: X-Plane's link leads to its folder all along, and its
  line in `scenery_packs.ini` stays as the user left it. Its put back is declared with the build's
  nodes, weighing its bytes copied then read back on another disk (`_put_back_rows`): the tile is
  in the build until it is back in its folder, and Works' bar and time left count the copy.
* Unchanged, its folder holds this very build already, its roads with it: nothing is copied,
  nothing is left in the workshop, and the step says already there. So it is when its folder
  holds the very files of the build and only their manifest differs (`lays_the_same`: the same
  DSF, textures and roads, sizes and decal): a neighbour built since, or its cache freed, gives
  the masks another key for the same bytes (their `nb_*` inputs), and the new manifest is written
  in the folder, nothing copied, X-Plane running or not (the whole tile was copied again, and
  refused while X-Plane ran, found on the owner's tiles, 2026-10-06). When only its roads are gone
  (a tile found again without them), they come back from the store, parked in it.
* Else, with X-Plane closed (`SYS_PUT_BACK_XP_RUNNING`, asked before the copy and again before
  the swap): its new roads and forests, which the build wrote in the workshop's overlays pack,
  are first parked in the new version (`park_roads`), where X-Plane does not read them; on the
  workshop's disk the new version is moved into place in one step, its images staying the
  cache's files; on another it is copied under `<name>.osxp-part`, its roads with it, sent to the
  disk as a group, read back (`SYS_TILE_COPY_DIFFERS`), with room for it and `ROOM_MARGIN`
  (`SYS_DISK_FULL`). Then the old version takes the name `<name>.osxp-old`, the new one its name,
  and the old one goes; its roads are put where the old ones were drawn, or left to another pack's
  when the old ones were, or the old ones go when the new version has none. Until that swap
  nothing of the old version is touched: a stop or a failure leaves it as it was, the new version
  back in the workshop, its roads parked in it (left beside it, they were drawn twice), and
  *Build again* finishes. What follows the swap cannot fail the put back.
* Then the workshop keeps nothing of the tile (the cache keeps its images), what a put back cut
  short left beside its folder goes, the Library lists it once, in its folder, as this build, and
  the X-Plane of Settings that shows it is given its install again, its line kept, or the install
  asked for when it did not show it; while that X-Plane runs, the tile it shows has its parked
  roads put beside it and its row, nothing of X-Plane changed. An old version a file held is said
  in Works, with where it is. Every X-Plane that showed it sees the new version, its link leading
  to the same folder.
* A Stop of the build (the job's callback raises `CancelRequested`) stops the put back under way
  before its next file, and the tiles after it are not put back; the build ends stopped, with its
  report, the other tiles settled.

Works shows the put back in the Installation cell, with how far the copy is in the cell's tooltip
(« retour à sa place : copie, 1,2 Go sur 4,1 Go »), no bar of its own: one more takes too much
room.

Acceptance (`tests/test_put_back.py`, fake X-Planes; the build's own tests run `build_tiles` with
a scheduler that answers every node as built, and the job's real Stop): where the new version
goes, by the rules above; on another disk copied, read back and in place of the old one, its new
roads where the old ones were drawn and none left in the workshop; on the workshop's disk moved
in one step, its images the cache's files; its new roads parked when the old ones were not drawn,
and the old ones gone with a new version that has none; X-Plane running, no room, a Stop and a
copy that reads back wrong leave the old version, the new one in the workshop with its roads
parked, no temporary folder; X-Plane started during the copy is seen before the swap; a move
refused at the swap goes back to the workshop; a put back cut between its two renames is mended
by the next build; what follows the swap cannot fail it; an old version a file kept is said, then
taken away by the next build; at the end of a build the tile is in its folder, X-Plane's line as
it was, one Library row, the workshop's copy and row gone, and built again unchanged nothing is
copied, X-Plane running or not, nor when only its manifest differs, the masks under another key
(`tests/test_put_back_same_files.py`, the store and packs as a build lays them out), another decal,
another texture or a folder not whole being other files; lost roads come back; the job's Stop ends the build stopped, the
tile after it settled and the stopped one not done; a tile on a disk away is not built and shows
nothing of it done; a tile filed elsewhere has no install node; the put back weighs its copy; the
rows of its folders gone follow it to the workshop.

## 5. Library (`library.py`)

Rule (new). `~/.orthostudio/library.sqlite` (root from `orthostudio.pipeline.home.osxp_home`) with
one table:

```
tiles(lat INTEGER, lon INTEGER, kind TEXT ('ortho'|'overlay'), path TEXT,
      provider TEXT, zl INTEGER, built_by TEXT ('osxp'|'ortho4xp'), keys TEXT (JSON or NULL),
      registered_at REAL, updated_at REAL, PRIMARY KEY (lat, lon, kind, path))
```

- `register(tile, provider, zl, path, built_by, keys=None, *, kind="ortho")`: upsert on
  the primary key (`registered_at` kept, `updated_at` refreshed). `path` is stored absolute:
  `osxp build --out tiles --install` registered a relative one, which `osxp serve` then read from
  its own working directory (`tests/test_library_register.py`). `keys` is the dict of
  store keys of the artefacts that made the pack (`None` for Ortho4XP builds: "installable, not
  incremental").
- `list(*, tile=None, kind=None)`: entries ordered by (lat, lon, kind, path).
- `forget(tile, *, kind=None, path=None)`: deletes the matching rows, returns the count
  (the delete of 4.2 forgets a pack's rows with it).
- Connection in WAL mode, `busy_timeout` 30 s, like the store index.

`import_ortho4xp(folder)`, `<ortho4xp>` being that folder:

1. Build roots: `<ortho4xp>/Tiles` (`O4_File_Names.py:20`), plus the Ortho4XP *custom build dir*
   when set. Ortho4XP keeps it in `<ortho4xp>/.last_gui_params.txt` line 2
   (`O4_GUI_Utils.py:359-368, 592-600`); a `custom_build_dir=` line in `Ortho4XP.cfg` is
   honoured too. Its Ortho4XP semantics (`O4_File_Names.py:62-68`): a trailing `/` means "the
   parent of `zOrtho4XP_*` directories", otherwise the value **is** one build directory.
2. In each root, every directory named `zOrtho4XP_*` is a pack; the tiles it holds are the
   `Earth nav data/<folder>/<tile>.dsf` files (a grouped pack lists several). For each
   tile, `provider`/`zl` come from `Ortho4XP_<tile>.cfg` (`tilefiles.tile_config`,
   `default_website`/`default_zl`); without a cfg they are read from the `.ter` file names
   (majority provider and ZL of `tilefiles.list_textures`); without either they are
   `""`/`0`. Registered with `built_by="ortho4xp"`, `keys=None`, `kind="ortho"`.
3. `<ortho4xp>/yOrtho4XP_Overlays/Earth nav data/*/*.dsf` (`O4_File_Names.py:22`): one
   `kind="overlay"` entry per tile, `provider=""`, `zl=0`.
4. Returns the entries imported, in scan order; re-running is idempotent (upsert).
   `SYS_WORKING_DIR_INVALID` when `folder` is not a directory.

Acceptance: register/list/forget round-trip including `keys`; a synthetic Ortho4XP tree
(`Tiles/zOrtho4XP_+43+005` with a tile cfg, a grouped `Tiles/zOrtho4XP_Group` with two DSFs and
no cfg, a custom build dir declared in `.last_gui_params.txt`, `yOrtho4XP_Overlays` with one DSF)
imports the entries with the expected provider/ZL, `built_by="ortho4xp"`, and writes nothing under
that tree. The import is the one place OrthoStudio XP reads an Ortho4XP folder (decision 0010).

## 6. Wanted differences from Ortho4XP

- Ortho4XP links a tile from the GUI only, never edits `scenery_packs.ini`, and does not detect
  X-Plane (the user types `custom_scenery_dir`). OrthoStudio XP does all three and refuses while
  X-Plane runs.
- Ortho4XP removes a link with `os.remove` without checking the ini; OrthoStudio XP removes the
  ini line.
- No `.bak` of a pack is made when linking (nothing is overwritten); `.bak` applies to
  `scenery_packs.ini` only.
