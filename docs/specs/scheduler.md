# Scheduler: asynchronous multi-tile DAG execution (`orthostudio.sched`)

Status: P2, written before the code of `src/orthostudio/sched/` (tests `tests/test_sched_*.py`).
Depends on `docs/specs/graph-keys.md` (rules, keys, store, write protocol) whose synchronous
`Executor` this module supersedes for real builds; the P0 executor stays for tests and for
worker-side single-node runs.

Origin in Ortho4XP: `Ortho4XP.py` / `O4_Tile_Utils.py:build_all` run the four steps of
one tile sequentially in one thread (`O4_Tile_Utils.py:build_tile` calls `build_mesh`,
`build_masks`, `build_dsf` in order, `O4_GUI_Utils.py` starts them in a single worker
thread); `O4_Imagery_Utils.py` uses `max_convert_slots` threads for `nvcompress`; masks use
`masks_build_slots` processes; batch mode (`build_tile_list`) is a `for` loop over tiles with
one tile in flight at a time. Decision: **replaced**, nothing to port. The measured baseline
uses 1.35 of 14 cores (`docs/benchmarks/baseline-ortho4xp.md`).

## 1. The rule in plain language

A build is a directed acyclic graph of **nodes**. A node applies a **rule** (P0 `Rule`) to
frozen **params** and to named **inputs** (other nodes, already-built artefacts, or absent).
The scheduler runs every node whose artefact is not already in the store, as soon as its
inputs are built, in the pool that fits its **kind**, within a memory budget, longest
remaining chain first, and reports typed events. Cancelling stops admission and asks running
nodes to stop; relaunching later costs nothing for what already succeeded.

## 2. Contract

```python
from orthostudio.model import ArtifactRef, TileRef

Node(id: str, rule: Rule, params: RuleParams,
     inputs: dict[str, Node | ArtifactRef | None],
     kind: Literal['cpu', 'net', 'subprocess', 'io'], ram_mb: int,
     run: Callable[[NodeContext], ArtifactRef] | None = None)

NodeContext: node_id, rule, params, key, recipe, inputs: dict[str, ArtifactRef | None],
             store: Store, workdir: Path, progress(fraction, message), cancel_event
             helpers: produce(writer) -> ArtifactRef, begin() -> Build, commit(build) -> ArtifactRef

Scheduler(store, *, cpu_workers: int | None = None, subprocess_slots: int = 1,
          net_slots: int = 2, io_slots: int = 4, ram_budget_mb: int | None = None,
          fail_fast: bool = False, workdir: Path | None = None, stats_interval_s: float = 1.0,
          lanes: Mapping[str, int] | None = None)
  add(node) -> Node          registers the node and every node reachable through its inputs
  plan(targets) -> list[PlanEntry]
  async run(targets: list[str], on_event=None) -> dict[str, ArtifactRef]
  cancel()                   thread-safe, idempotent
  failed: dict[str, OsxpError]   after run(): every node that did not produce an artefact
  costs: CostModel

Events: Started(node_id, kind, key) · Progress(node_id, fraction, message) ·
        Done(node_id, key, hit, wall_s, ref) · Failed(node_id, error, cause) ·
        Stats(running, pending, done, failed, hits, elapsed_s, eta_s)
```

`ArtifactRef(key, digest, path, rule, kind, size)` lives in `orthostudio.model` with `TileRef`.

### 2.1 Node identity and validation

* `id` is unique in a scheduler (`"+43+005/mesh"`); adding the **same object** twice is a
  no-op, adding a different node under an existing id is `InvalidNodeError`.
* `inputs` must name exactly the rule's declared inputs (P0 invariant); a value is a `Node`
  (edge of the DAG), an `ArtifactRef` (already built, e.g. imported or from another run) or
  `None` (absent, part of the key). Cycles are detected at `run()` (`InvalidNodeError`).
* `run` is what the node does when its key is not in the store. It receives a `NodeContext`
  and must return the `ArtifactRef` of the committed artefact **at `ctx.key`** (the scheduler
  refuses a different key: `SYS_INTERNAL_ERROR`). When `run` is `None`, the scheduler runs
  `rule.fn` through the P0 write protocol (`RunContext`, `begin`/`commit`). For `cpu` nodes
  executed in worker processes, `run`, `rule` and `params` must be picklable (module-level
  functions, frozen pydantic models).

### 2.2 Keys, hits, deduplication, early cutoff

* The key of a node is `key_for(rule, params, {name: digest of the resolved input})`,
  computed when the node becomes ready (its inputs are built), never before: P0 section 8
  applies unchanged.
* **Hit**: `store.has(key)` -> `touch` (LRU, edges re-pointed) and `Done(hit=True)` with no
  execution and no pool slot. Relaunching a build after a crash therefore replays instantly
  up to the first node whose artefact is missing; a `<key>.tmp-*` left by a crashed run is
  invisible to `has()` (P0 section 6), so a partial artefact is never taken for a hit.
* **Deduplication**: two nodes of the run with the same key (two tiles sharing a neighbour
  mesh, two objects with the same recipe) execute once; the second is reported
  `Done(hit=True)` when the first commits. Two *processes* may still build the same key
  (P0: one adopts).
* **Early cutoff** follows from keying by digest: when a rebuilt upstream produces the same
  bytes, every downstream key is unchanged and hits. No scheduler logic is involved
  (`test_early_cutoff_stops_downstream`).

### 2.3 Pools and admission

| kind | executes in | limit | typical rule |
|---|---|---|---|
| `cpu` | `ProcessPoolExecutor` (`spawn`), `cpu_workers` = `os.cpu_count() - 2` (min 1) | `cpu_workers` | masks, DDS encoding, noding, DSF encoding |
| `subprocess` | thread of the shared pool, the node spawns and owns its child process | `subprocess_slots` | Triangle4XP, Ortho4XP stages, DSFTool |
| `net` | thread of the shared pool; the node drives its own `Fetcher` | `net_slots` | chunk downloads, OSM, DEM (each slot is one network pipeline of 64-128 requests) |

A running node may **lend its slot while it waits** (`NodeContext.idle()`, a context manager): the count of its pool drops, another node of that kind may start, and the waiting one takes its slot back without queueing when it resumes -- so that pool can hold one extra node until it finishes. It exists for the spaced retry rounds of the textures (`pipeline-textures.md` 4.1): a build has one network slot, and a tile waiting for a handful of stuck image pieces left the whole batch's line idle (a user, 2026-09-18). A node that ends while idle is counted once, not twice.
| `io` | thread of the shared pool | `io_slots` | linking, copying, `.ter` writing, install |

**Lanes.** A node may name a lane (`Node(lane="overpass")`, a thread kind only): it then counts
against that lane's limit (`Scheduler(lanes={"overpass": 1})`) instead of its kind's slots. The
OSM downloads of a build share the Overpass mirrors' quota, not the imagery's network pipeline:
on one `net` slot with the images, a tile's download waited for another tile's images
(`pipeline-build.md` 4). A lane the scheduler has no limit for is refused when the node is added.

`cpu_workers=0` runs `cpu` nodes in threads (tests and debugging: no spawn, plain
tracebacks). A node is **admitted** when a slot of its kind (or its lane) is free and
`Σ ram_mb of running nodes + node.ram_mb <= ram_budget_mb`. A node alone larger than the
budget is admitted when nothing is running (refusing would deadlock the build; the plan
already warned). `ram_budget_mb=None` disables the memory check. The thread pool is sized to
`subprocess_slots + net_slots + io_slots + the lanes' limits (+ cpu_workers in thread mode)` so a thread is
always available for an admitted node.

Workers ignore `SIGINT` (the parent handles Ctrl-C) and open their own `Store` on the same
root (one sqlite connection per process, as `pipeline/textures.py` does).

### 2.4 Priority: remaining critical path

`priority(n) = cost(n) + max(priority(d) for d in dependants of n)` (0 for a sink), where
`cost` is the estimated seconds of the rule (section 2.7). Ready nodes are admitted in
decreasing priority; ties by insertion order (deterministic). A three-node chain of default
costs therefore starts before an unrelated single node added earlier
(`test_critical_path_starts_first`).

### 2.5 Events, progress, statistics

All events are delivered **in the event-loop thread** by `on_event`, in causal order per
node: `Started` -> `Progress*` -> `Done | Failed`. Worker threads post through
`loop.call_soon_threadsafe`; worker processes write `(node_id, fraction, message)` to a
`multiprocessing.Queue` drained by a parent thread that posts the same way. `progress` is
best effort: a node should call it at most a few times per second; the scheduler never
blocks on it.

`Stats` is emitted after every `Done`/`Failed` and every `stats_interval_s` while nodes run:
`running`, `pending` (not yet started, not skipped), `done` (built + hits), `failed`, `hits`,
`elapsed_s`, and `eta_s = remaining_cost / parallelism` where `remaining_cost` is the sum of
the estimated costs of pending nodes plus the unfinished fraction of running nodes (from
their last `Progress`, else full cost), and `parallelism` is the observed mean concurrency
`Σ busy seconds / elapsed` (at least 1). Hits cost nothing and are not counted. `eta_s` is
always a number, optimistic before the first observations; the UI turns it into a bracket
with the pessimistic side of `plan()` (2.8).

### 2.6 Failure, propagation, cancellation

* A node that raises is `Failed(node_id, error)` with `error = orthostudio.errors.wrap(exc)`
  (`OsxpError` kept as is, anything else becomes `SYS_INTERNAL_ERROR`). Its workdir is kept
  for inspection; a successful node's workdir is removed.
* Every transitive dependant of a failed node is `Failed(node_id, error, cause=<failed id>)`
  with `error.code == "SYS_CANCELLED"` and `error.context["upstream"]` naming the culprit and
  its code; it never starts. Independent branches continue. `fail_fast=True` cancels the whole
  run at the first failure instead.
* `cancel()` (from any thread, also wired by the CLI to the first `SIGINT`) stops admission,
  sets the cooperative `cancel_event` seen by running nodes (a `threading.Event` in threads,
  a `multiprocessing.Event` in worker processes; nodes poll it or pass it to their child
  process management, which kills the child), waits up to `cancel_grace_s` (5 s) for them,
  then terminates the worker processes (`ProcessPoolExecutor.terminate_workers`, 3.14; the
  private process table before). Pending nodes are `Failed` with `SYS_CANCELLED`. A cancel
  with cooperative nodes returns in well under one second (`test_cancel_under_one_second`).
* `run()` returns the refs of the **targets that succeeded**; `scheduler.failed` holds the
  error of every node that did not; a caller that wants an exception checks it. Nothing
  partial is ever indexed (P0 write protocol).
* Cancelling the `run()` task itself (the CLI's second Ctrl-C) abandons the run **at once**:
  `cancel()` is set and the nodes still running are failed with `SYS_CANCELLED` without a
  second grace period (`_settle_cancel(grace_s=0)`), then the pools are shut down without
  waiting. Any other `BaseException` escaping the loop (`KeyboardInterrupt` when no signal
  handler is installed, an `on_event` callback raising one) is treated as the first Ctrl-C:
  `cancel()`, the grace period, then re-raised; the running nodes are never left to finish
  on their own (P2a review).
* A node that commits under a wrong key (`KeyMismatch`, `SYS_INTERNAL_ERROR`) also fails its
  in-flight twins (the nodes waiting on the same key) with that node as their cause, instead
  of leaving them to the stall detector.

### 2.7 Cost model (EWMA, persisted)

`CostModel(store)` keeps one estimate per `"<rule>@<version>"`: `ewma <- 0.3 * wall +
0.7 * ewma` (first observation replaces the default of **1.0 s**); hits are not observed.
Estimates are read at scheduler construction from the store's `meta` table (rows
`sched.cost.<rule>@<version>` = `{"ewma": s, "n": count}`) and written back at the end of
`run()`; they survive processes and drive both priority (2.4) and ETA (2.5). The store
exposes no public meta accessor in P0, so `costs.py` uses the store's connection under its
lock (to be replaced by `Store.meta_get/meta_set` when `orthostudio.graph` grows them).

### 2.8 `plan(targets)`

Without running anything: for each node in the closure of `targets`, its key and `hit` /
`build` / `unknown` (an input not built yet, P0 section 8) plus the estimated seconds; the
sum of `build` and `unknown` estimates is the pessimistic side of the ETA bracket shown by
`osxp plan`.

## 3. Acceptance tests (`tests/test_sched_scheduler.py`, no network, no Ortho4XP)

Fake rules sleep in small slices while polling `cancel_event`, write small files, and
record their start/end instants in a shared log.

| # | Test | Asserts |
|---|---|---|
| S1 | overlap of kinds | 3 cpu + 2 subprocess + 2 io nodes of 0.2 s each finish in < 0.5 s total |
| S2 | cpu limit | 8 cpu nodes, `cpu_workers=3`: max concurrent cpu = 3 |
| S3 | subprocess / net limits | never more than `subprocess_slots` / `net_slots` in flight |
| S4 | RAM budget | 4 nodes of 600 MB, budget 1000: max concurrent = 1; budget 1300: 2; oversized node admitted alone |
| S5 | critical path | chain of 3 added after a lone node starts first with `cpu_workers=1` |
| S6 | hit / miss | second run of the same graph: every node `Done(hit=True)`, no `run` called |
| S7 | early cutoff | upstream param change with identical bytes rebuilds only the upstream |
| S8 | cancel | 6 nodes of 2 s, cancel at 0.2 s: `run()` returns in < 1 s, pending nodes `SYS_CANCELLED`, nothing indexed for the interrupted ones |
| S9 | failure | A fails, B(A) `Failed(cause=A)` never started, C independent `Done`; `fail_fast` cancels C |
| S10 | resume after crash | a run whose node leaves `<key>.tmp-*` and raises: rerun hits the finished nodes and rebuilds that node |
| S11 | dedup across tiles | two tile graphs sharing one recipe under different ids: one execution, both `Done`, targets of both tiles returned |
| S12 | process pool | `cpu_workers=2`: nodes run in other pids, `Progress` from processes arrives, an `OsxpError` raised in a worker keeps its code |
| S13 | events | order `Started` -> `Progress` -> `Done`, all delivered in the loop thread, `Stats` counts consistent |
| S14 | costs | EWMA persisted in the store meta and reloaded by a new scheduler; priority follows it |
| S15 | `run=None` | a P0-style rule with `fn` runs through the write protocol |

Benchmark (`tests/test_sched_bench.py`, printed, loose bound): 200 trivial nodes in thread
mode, build then hit, per-node overhead reported in section 5.

## 4. Wanted differences from Ortho4XP

Everything: Ortho4XP has no graph, no cache, no priority, no budget, no cancellation short of
killing the process, and its progress is `done / (done + queue size)`.

## 5. Measurements

M4 Pro 14 cores / 48 GB, macOS, Python 3.14, 2026-09-12 09:44, uptime 5 days, load average
2.1-2.9, `nice -n 10`. 200 trivial nodes (20 chains of 10, one `write_bytes` each), 4 cpu
slots, `fsync=False`; medians of 3 runs (thread mode) and 5 runs (process mode).

| Mode | Build (200 nodes) | Per built node | Hit (200 nodes) | Per hit |
|---|---|---|---|---|
| threads (`cpu_in_threads=True`), `uv run pytest tests/test_sched_bench.py -s` | 97 ms | 0.48 ms | 19 ms | 0.10 ms |
| processes (`spawn`, 4 workers, includes pool start-up), scratch script | 214 ms | 1.07 ms | 20 ms | 0.10 ms |

600 events per run (Started, Done, Stats per node). Against a 60-250 s tile the scheduler
costs well under 0.3 s per tile; a hit replay of a whole tile graph (~300 nodes at ZL16:
one per texture plus the stages) is ~30 ms. Process spawn start-up is ~45 ms for the pool
plus the import of `orthostudio.sched.workers` in each worker (no numpy on that path).

Cancellation (`test_cancel_under_one_second`): 6 nodes of 2 s on 2 slots, cancel at 0.2 s,
`run()` returns in ~0.25 s in thread mode; in process mode (`test_process_pool_cancel`,
3 nodes of 5 s) the cooperative stop returns in ~0.5 s after the cancel.

## 6. Implementation notes (deviations from the contract, all additive)

* `Scheduler(..., cpu_in_threads=False, io_slots=4, cancel_grace_s=5.0, workdir=None,
  stats_interval_s=1.0, costs=None)`: extra keyword arguments; `cpu_in_threads=True` is the
  test mode (2.3). `cpu_workers` keeps its contract meaning.
* `Started` carries `key`; `Done` carries `ref`; `Failed` carries `cause`; `Stats` carries
  `failed` and `hits` in addition to the contract fields.
* `Node.run` may be `None` (P0 rule through the write protocol); `Node.ram_mb` may be `None`
  (the rule's declaration). `NodeContext` adds `key`, `recipe`, `node_id`, `rule`, `params`
  and the helpers `produce` / `begin` / `commit` / `existing` / `check_cancelled`.
* A worker-side failure keeps its traceback in `error.context["traceback"]`.
* `ArtifactRef` is defined in `orthostudio.model` (the contract uses it without a home).
* `CostModel` reads and writes the store's `meta` table through the store's private
  connection (2.7); a public `Store.meta_get/meta_set` in `orthostudio.graph` would remove that.
