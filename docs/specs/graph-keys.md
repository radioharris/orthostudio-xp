# Artefact graph: keys, store layout, index schema

Status: P0, implemented in `src/orthostudio/graph/` (tests `tests/test_graph_*.py`).
Origin: invented here. Ortho4XP has no memory of work done: masks are deleted and
rebuilt on every run (`O4_Mask_Utils.py:248-256`) and textures are only compared to their
mask by mtime (`O4_DSF_Utils.py:730-731`). Decision: new mechanism, nothing to port.

## 1. Vocabulary

| Term | Meaning |
|---|---|
| **rule** | A named, versioned computation: `Rule(name, version, params, inputs, ram_mb, kind, fn)`. |
| **params** | The *subset* of configuration a rule consumes, as a frozen pydantic model (`RuleParams`). `rule.bind(config)` picks those fields out of the full configuration and ignores the rest. |
| **input** | A named dependency of a rule. At run time it is an artefact of another rule, a *source*, or absent (`None`). |
| **source** | An external input identified by the digest of its bytes (a downloaded file, a DEM, an edited JOSM file). `Source.from_path`, `Source.from_bytes`. |
| **digest** | blake3 of an artefact's *content* (64 lowercase hex). For a directory: blake3 of its manifest (section 5). |
| **key** | blake3 of the *recipe* (rule, version, params, input digests). The address of an artefact in the store. |
| **artefact** | A file or a directory produced by one rule application, stored under its key. |
| **pin** | A named root (`tile:+43+005`) the garbage collector must keep, with everything it transitively uses. |

An artefact therefore has two identities: its **key** says *how it was made*, its **digest**
says *what it contains*. Dependents reference inputs by **digest**. That single choice gives
early cutoff (section 8) without any extra machinery.

## 2. Rule declaration

* `name` matches `^[a-z][a-z0-9_.-]{0,63}$` (it is a directory name).
* `version` is an integer >= 0, bumped whenever the rule's output may change for identical
  inputs and params (algorithm change, bug fix, format change). It is the only escape hatch:
  there is no "clear cache" needed after a code change.
* `params` is a `RuleParams` subclass: frozen, `extra="forbid"` (a typo in a field name is an
  error), every field typed. The consumed subset is `rule.consumed == tuple(model_fields)`.
* `inputs` are unique names matching `^[a-z][a-z0-9_]{0,63}$`; stored sorted. Every input
  must be given at node construction, possibly as `None` ("absent", e.g. a neighbour tile
  that is not built), which is a distinct, keyed state.
* `ram_mb` is the declared peak resident memory of one run. The synchronous executor only
  refuses a rule whose declaration exceeds its optional `ram_budget_mb`; the P2 scheduler
  will use it for admission control.
* `kind` is `"file"` or `"dir"`: what `fn` must leave at `ctx.out`.

## 3. Key format

The key hashes a **recipe document**:

```json
{"format":"osxp-key-1","inputs":{"dem":null,"osm":"<64 hex>"},"params":{...},"rule":"vectors","version":1}
```

`key = blake3(canonical_json(document)).hexdigest()`.

`params` is `model.model_dump(mode="json")` of the rule's params (so `Path`, enums, tuples are
already JSON scalars) and `inputs` maps every declared input name to the digest of what was
actually consumed, or `null`.

### Canonical JSON (`orthostudio.graph.canon`)

* objects: keys must be strings, emitted sorted by Unicode code point, no whitespace;
* arrays: lists and tuples, in order;
* strings: JSON with `ensure_ascii` (pure ASCII bytes);
* integers: decimal, unbounded; booleans are `true`/`false`, never `1`/`0`;
* floats: Python's shortest round-trip `repr` (`1e-09`, `1e+16`, `2.0`); `-0.0` is folded to
  `0.0`; NaN and infinities are rejected (`CanonError`); `1` and `1.0` are **distinct**;
* `None` is `null`; every other type is rejected.

Worked example (frozen as `GOLDEN_KEY` in `tests/test_graph_keys.py`):

```
rule=vectors version=1
params={"road_level": 1, "curvature_tol": 2.0, "name": "Marseille é"}
inputs={"osm": "00"*32, "dem": None}
recipe: {"format":"osxp-key-1","inputs":{"dem":null,"osm":"000...000"},"params":{"curvature_tol":2.0,"name":"Marseille é","road_level":1},"rule":"vectors","version":1}
key:    d03932ed4b65b02bed14b81d358f79e830c6bb6c147dda1c238437af2c6d782f
```

### Invariants

| # | Invariant | Test |
|---|---|---|
| K1 | The key of a recipe is identical in any process, any `PYTHONHASHSEED`, any dict insertion order. | `test_key_is_stable_across_processes_and_hash_seeds`, hypothesis in `test_graph_canon.py` |
| K2 | Only the consumed subset enters the key: changing a configuration field a rule does not declare leaves its key unchanged. | `test_unconsumed_parameter_changes_nothing` |
| K3 | Inputs enter the key by content digest, never by key or path. | `test_early_cutoff_stops_at_identical_bytes` |
| K4 | An absent input (`null`) and a present one always give different keys. | `test_source_content_and_absent_inputs_are_part_of_the_key` |
| K5 | Rule name and version are part of the key. | `test_every_ingredient_changes_the_key` |
| K6 | A key never equals a digest by construction (different preimages); two keys may share one digest (same bytes, different recipes). | `test_iter_artifacts_and_keys_with_digest` |
| K7 | The stored recipe of a key re-hashes to that key (`why` is self-verifying). | `test_golden_key_and_recipe` |

Changing the canonical format or the document layout changes `"format"`, hence every key:
that is the intended "flag day", never a silent re-interpretation.

## 4. Disk layout

```
<root>/
  index.sqlite  (+ -wal, -shm)
  <rule>/<key[:2]>/<key>                      the artefact: a file (kind=file) or a directory (kind=dir)
  <rule>/<key[:2]>/<key>.tmp-<pid>-<rand8>/   one build in progress
      out                                     what the rule writes (file or directory)
      scratch/                                free working space, removed with the tmp dir
```

Two hex characters of sharding keep a rule directory under 256 entries per level for stores
of up to ~100 000 artefacts per rule. Paths are ASCII and below 120 characters plus the root
(Windows `MAX_PATH` safe). Symbolic links inside artefacts are refused (a digest must cover
bytes, not names).

## 5. Digests (`orthostudio.graph.digest`)

* file: blake3 of the bytes, memory-mapped, multi-threaded (`blake3.AUTO`);
* directory: blake3 of a manifest `osxp-dir-manifest-1\n` followed by one line
  `relpath\0size\0blake3(file)\n` per regular file, sorted by POSIX relative path. Empty
  directories are not part of the identity; permissions and mtimes are not either.

Measured on the reference Mac (M4 Pro, load 5): 22 MB in 3 ms, 268 MB (a whole ZL14 tile of
DDS) in 20 ms, i.e. digesting is never the bottleneck of a build.

## 6. Write protocol and crash matrix

1. `store.begin(rule, key, kind)` creates the tmp dir, `out` (an empty directory for
   `kind=dir`) and `scratch/`.
2. The rule writes `out`.
3. `commit()`: validate the kind; fsync every written file (unless `Store(fsync=False)`);
   compute digest and size; if `<key>` already exists on disk, **adopt** it (our output is
   discarded, the on-disk one is digested and indexed: another process won the race, or a
   crash had left a renamed artefact without its row); else `os.rename(out, <key>)`
   (atomic on POSIX and NTFS when the target does not exist); fsync the shard directory;
   upsert the index row and the edges in one sqlite transaction; remove the tmp dir.
4. `abort()` or an exception removes the tmp dir; nothing reaches the index.

| Crash point | State on disk | Recovery |
|---|---|---|
| during 2 or before the rename | `<key>.tmp-*` left behind | ignored by every reader (not indexed, name never matches a key); swept at `Store()` open when older than `tmp_max_age_s` (1 h) **and** the owner pid of the name is gone (`os.kill(pid, 0)`; `OpenProcess` on Windows), or older than `tmp_hard_max_age_s` (24 h) whatever the owner (a reused pid must not pin it), or by `fsck(repair=True)`; a build in progress in another process is never swept (P2a review) |
| after the rename, before the index row | `<key>` present, no row | `has()` is False; the next build of the key adopts it; `fsck` lists it under `files_without_rows` |
| row present, files removed by hand | row without files | `has()` deletes the row and returns False (self-healing) |
| two processes build the same key | one renames, the other adopts | both report the same digest; `uses` counts both (`test_concurrent_processes_building_the_same_key_agree`) |
| corrupted content | digest mismatch | `fsck(verify=True)` reports it; `repair=True` deletes it |

The executor never trusts a path that is not both indexed and present.

## 7. SQLite index (`schema_version = 1`, WAL, `synchronous=NORMAL`, `foreign_keys=ON`)

```sql
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);           -- schema_version
CREATE TABLE artifacts (
    key          TEXT PRIMARY KEY,   -- 64 hex
    rule         TEXT NOT NULL,
    version      INTEGER NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('file', 'dir')),
    digest       TEXT NOT NULL,      -- content digest, 64 hex
    size         INTEGER NOT NULL,   -- bytes (sum of files for a directory)
    created_at   REAL NOT NULL,      -- unix time
    last_used_at REAL NOT NULL,      -- LRU for the collector
    uses         INTEGER NOT NULL DEFAULT 1,
    recipe       TEXT NOT NULL       -- canonical recipe document (frozen params included)
);
CREATE INDEX artifacts_rule ON artifacts (rule);
CREATE INDEX artifacts_rule_digest ON artifacts (rule, digest);    -- early-cutoff witnesses
CREATE INDEX artifacts_lru ON artifacts (last_used_at);
CREATE TABLE edges (                 -- child consumed input `name` with this digest
    child      TEXT NOT NULL REFERENCES artifacts (key) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    digest     TEXT,                 -- NULL for an absent input
    parent_key TEXT REFERENCES artifacts (key) ON DELETE SET NULL,  -- NULL for a source
    PRIMARY KEY (child, name)
);
CREATE INDEX edges_parent ON edges (parent_key);
CREATE TABLE pins (
    name      TEXT PRIMARY KEY,      -- e.g. "tile:+43+005"
    key       TEXT NOT NULL REFERENCES artifacts (key) ON DELETE CASCADE,
    pinned_at REAL NOT NULL
);
```

`refcount(key) = pins on key + edges whose parent_key is key`. A different schema version
raises `IncompatibleIndexError` at open (no migration in P0). One connection per `Store`,
guarded by an `RLock`; several processes share the file through WAL and `busy_timeout`.
Opening is retried for up to 15 s: when several processes create the same index at the same
instant, the WAL switch can fail with "database is locked" without consulting the busy
handler (observed once in five runs of the four-process race test before the retry).

## 8. Executor semantics (`orthostudio.graph.executor`)

* A `Node` is a rule, an instance of its params model and a mapping of every declared input
  to a `Node`, a `Source` or `None`. Nodes are compared by identity; two node objects with
  the same recipe resolve to the same key (built once, hit once).
* `Executor.run(target, pin=...)` resolves depth-first, inputs in name order, one node at a
  time. For each node: compute the key from the input *digests*; if `store.has(key)`,
  **hit**: `touch` it (LRU, `uses`, and the edges are re-pointed to the parent keys actually
  used in this run); else **build** it through the write protocol. `RuleFailedError` wraps
  any exception of the rule; the tmp dir is gone and the index untouched.
* **Early cutoff.** After a build, `identical_to_previous` is True when another key of the
  same rule already holds the same digest. Because downstream keys hash digests, those
  downstream nodes then hit: a `road_level` change that re-nodes the vectors to the same
  bytes rebuilds `vectors` only (`test_early_cutoff_stops_at_identical_bytes`).
* **Scoped invalidation.** A parameter change rebuilds exactly the rules that declare it and
  everything downstream of an actually changed digest
  (`test_parameter_change_invalidates_only_consumers_downstream`).
* `plan(target)` computes keys without building: each node is `hit`, `build`, or `unknown`
  when one of its inputs is not built yet (its digest cannot be known before the build).
  This is the honest limit of digest-keyed inputs: an estimate can only be certain one level
  past what already exists; deeper levels are "at most".
* Events: `NodeStarted(node, key)` and `NodeFinished(node, result)`; `NodeResult` carries
  key, digest, path, status, seconds, size.

## 9. Garbage collection

`store.gc(quota_bytes=None, min_age_s=0)`: candidates are artefacts with `refcount == 0`
older than `min_age_s`, taken least-recently-used first. Without a quota every candidate is
deleted and the loop repeats until none remains (deleting a child frees its parents:
reachability from pins by cascade). With a quota the loop stops as soon as the total size
fits. Rows are deleted before files; a crash in between leaves an orphan that `fsck` finds.
Pins are the only intent the collector respects: `Executor.run(..., pin="tile:+43+005")`
pins the target; `unpin` releases it. 200 artefacts collect in 25 ms.

### 9.1 `osxp clean` (`src/orthostudio/clean.py`)

`gc()` alone cannot serve a user: a build pins nothing, and a `texture.dds` artefact is the input of
no other artefact, so it would collect every texture of every installed tile. `OrthoStudio XP clean`
collects by reachability from the packs on disk instead:

* **roots**: the artefact keys in the `orthostudio.toml` of every pack the library records and of
  every pack under `<data folder>/tiles` (`$OSXP_HOME` unless Settings chose another data folder);
* **kept**: the roots, everything they were built from (`Store.reachable`, the recorded
  inputs walked transitively), the `texture.dds` keys listed by the kept `tile.textures`
  (their `manifest.json`), anything pinned, and anything used in the last hour (a build in
  another process may not have recorded its edges yet);
* **removed**: everything else, then the abandoned temporary directories (`sweep_tmp`);
* **reported size**: the bytes the disk gets back, inode by inode. A file counts when all its
  hard links are among the deleted paths; a DDS still linked from a pack frees nothing;
* `--images` also empties `<data folder>/chunks`, the raw imagery a rebuilt texture would otherwise
  download again, and `<data folder>/mapcache`, the base-map tiles of the page (`map-zones.md` section
  6), both counted in the imagery size of the report; `--dry-run` deletes nothing and reports the
  same numbers. `--all` frees everything OrthoStudio XP can give back: it collects without the hour
  of grace (a result used a second ago goes too, unless a pack on disk needs it) and implies
  `--images`. It refuses to run while another process builds into the store (`Store.building_pids`,
  read from the `*.tmp-<pid>-*` directories of builds in progress), since the grace period is what
  protects such a build otherwise. The command passes the map cache explicitly (`mapcache_root`);
  `clean()` called without it counts and empties no map cache. It used to take the `mapcache` beside
  the chunks root for one, so a caller passing its own chunks folder had the folder next to it
  emptied (review of 2026-09-13);
* **after a delete**: the page's Delete and `osxp uninstall --delete` run a narrower collection
  right after deleting a pack (`clean_after_delete`, `install.md` 4.2). The candidates are the keys
  of the deleted pack's `orthostudio.toml` and what they need (`needed_keys`, the same walk as the
  roots of `osxp clean`); a candidate stays when a remaining pack needs it, when it is pinned, or
  when it was used in the last ten minutes (`DELETE_GRACE_S`: an artefact touched that recently may
  be shared with a build running in another process). Nothing else is looked at, and temporary
  directories are not swept: superseded builds stay for `osxp clean`, and the deleted tile is
  credited in `freed_bytes` with its own cache only. Running the whole clean there took other
  processes' finished artefacts that no pack referenced yet (review of the delete);
* **sizes shown**: `disk_bytes` measures a folder the same way, each inode once: the page's
  store size, which the index's sum put at twice the disk's (a DDS linked by two artefacts),
  and the size of each pack in the library (`api.md` 2.3).

Measured on the reference machine, 2026-09-13: 5 installed tiles, 122 of 1 827 artefacts
removed (the flat `+46+006` of decision 0007 and two benchmark builds), 1.7 GB freed, every
pack still intact (`pack_is_intact`).

## 10. `why(key)`

Returns a `Provenance`: index row, frozen params, inputs `(name, digest, parent key or
None)`, pins and dependents `(child key, input name)`. `explain(key, depth=n)` renders it:

```
masks@1  key 17d055c71c15  digest 5a57127d506c  33 B  file  built 2026-09-12T02:46:01  used 1x  pins: tile:+43+005
  params  {"masks_width": 100}
  inputs
    mesh  <- mesh@1 key 3b0f...  digest 9c1e...
      mesh@1  key 3b0f...  ...
        params  {"curvature_tol": 2.0}
        inputs
          vectors  <- vectors@1 key 4ba8...  digest 3107...
    nb_n  <- absent
```

The recipe is stored inline, so `why` still answers after the inputs were collected.

## 11. Measurements (M4 Pro, load average 5, `nice -n 10`, 2026-09-12)

| Operation | Cost |
|---|---|
| `artifact_key` (10 params, 6 inputs) | 21 us |
| `has()` | 15 us |
| commit of a 1 KiB file, fsync on / off | 0.48 / 0.45 ms |
| commit of 22 MB (one DXT5 texture) | 3 ms |
| commit of 268 MB (all DDS of a ZL14 tile) | 20 ms |
| executor, per node, hit / build (trivial rule) | 0.07 / 0.51 ms |
| `plan` over 200 nodes | 4 ms |

Against a 60-100 s tile build, the graph costs well under 0.1 s per tile.

## 12. Not there yet

* **Scheduler**: asyncio, CPU/network/disk pools, RAM admission, cancellation, critical
  path, EWMA-weighted progress (P2). The executor here is single-threaded and synchronous.
* **Variadic inputs**: every input name is fixed at rule declaration; the 8 neighbours of a
  mask are eight named inputs, some `None`.
* **Locking of concurrent builds**: two processes may build the same key twice (one adopts);
  a build lock or a "pending" row would avoid the duplicate work.
* **Dedupe by digest** (hard links between keys sharing a digest) and **compression** of
  artefacts (zstd is a dependency, unused here).
* **Index migrations**: a schema change is a hard error, not a migration.
* **Quota policy tied to free disk space** (plan: <= 20 % of the free space), size by
  category, purge by region: these are `osxp cache` features on top of `gc()`.
* **Configuration coverage test**: every Ortho4XP `cfg_vars` parameter consumed by some rule or
  marked obsolete (PLAN section 2) needs the rules of P1-P4 to exist first.
* **Remote or shared stores**, read-through of legacy `Orthophotos/` and AutoOrtho caches
  belong to `orthostudio.imagery`, not to the store.
