# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The store's index is let go after each build and each plan.

Found on the owner's Mac (2026-10-06): the data folder moved to another disk in Settings, the old
one could not be ejected while the app ran ("dissented by" its Python): every build and every plan
had opened the store's index through its build environment, and only Python collecting the
environment closed it. The environments here are the real ones, on a fake X-Plane.
"""

from __future__ import annotations

import gc
import sqlite3
import time
import weakref
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

import test_api_fakes as fakes
from orthostudio.api import JobManager
from orthostudio.api.app import create_app
from orthostudio.graph import Store
from orthostudio.pipeline.build import BuildEnv, BuildSpec
from test_api_fakes import FakeBuild, FakeIndex, client_for, make_spec

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane


def _closed(store: Store) -> bool:
    try:
        store.info("0" * 64)
    except sqlite3.ProgrammingError:
        return True
    return False


def _recording(envs: list[BuildEnv]) -> Callable[[Sequence[BuildSpec]], BuildEnv]:
    def factory(specs: Sequence[BuildSpec]) -> BuildEnv:
        envs.append(BuildEnv.create(specs))
        return envs[-1]

    return factory


@pytest.mark.parametrize("fail", [{}, {"mesh": "MESH_TRIANGULATION_FAILED"}])
def test_a_job_lets_go_of_the_store_it_opened(
    home: Path, xplane: Path, fail: dict[str, str]
) -> None:
    envs: list[BuildEnv] = []
    mgr = JobManager(
        jobs_dir=home / "jobs", build=FakeBuild(fail=fail), env_factory=_recording(envs)
    )
    job = mgr.start([make_spec(home=home)], install=False, request={"tiles": ["+43+005"]})
    assert job.wait(10.0)
    assert job.state()["status"] == ("failed" if fail else "done")
    (env,) = envs
    assert _closed(env.store)
    mgr.close()


@pytest.mark.anyio
async def test_a_plan_lets_go_of_the_store_it_opened(home: Path, xplane: Path) -> None:
    envs: list[BuildEnv] = []
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=None)
    app = create_app(
        env_factory=_recording(envs), jobs=mgr, airports=FakeIndex(),
        settings_path=home / "config.toml",
    )  # fmt: skip
    async with client_for(app) as c:
        r = await c.post("/api/plan", json={"tiles": ["+43+005"], "zoom_level": 14})
    assert r.status_code == 200, r.text
    (env,) = envs
    assert _closed(env.store)
    mgr.close()


def test_a_store_given_to_the_environment_stays_open(home: Path, xplane: Path) -> None:
    """The caller's store is the caller's to close."""
    spec = make_spec(home=home)
    with Store(spec.store_root, fsync=False) as store:
        env = BuildEnv.create([spec], store=store)
        env.close()
        assert not _closed(store)


def test_after_a_stop_the_store_stays_open_for_the_steps_still_running(
    home: Path, xplane: Path
) -> None:
    """On a Stop the scheduler waits a few seconds, then leaves the steps still running: one that
    commits later found the index closed, its download moved into the store with no row, fetched
    again by the next build (a review, 2026-10-06). After a Stop the store is left to them."""
    envs: list[BuildEnv] = []
    mgr = JobManager(
        jobs_dir=home / "jobs", build=FakeBuild(delay_s=0.05), env_factory=_recording(envs)
    )
    job = mgr.start([make_spec(home=home)], install=False, request={"tiles": ["+43+005"]})
    time.sleep(0.12)
    assert mgr.cancel(job.id)
    assert job.wait(10.0) and job.status == "cancelled"
    (env,) = envs
    assert not _closed(env.store)
    env.store.close()
    mgr.close()


def test_a_build_that_fails_lets_its_store_go(home: Path, xplane: Path) -> None:
    """A build that raised kept its environment, and the store's index it opened, until Python
    looked for cycles: the error's traceback held the job's frame (a review, 2026-10-06). It goes
    as soon as nothing uses it, without waiting for that."""
    from orthostudio.errors import OsxpError

    stores: list[weakref.ref[Store]] = []

    def factory(specs: Sequence[BuildSpec]) -> BuildEnv:
        env = BuildEnv.create(specs)
        stores.append(weakref.ref(env.store))
        return env

    def fails(specs: Sequence[BuildSpec], *, on_event: object, env: object) -> None:
        raise OsxpError("SYS_INTERNAL_ERROR", context={"detail": "refused at its start"})

    gc.disable()  # what is freed here is freed by the references alone
    try:
        mgr = JobManager(jobs_dir=home / "jobs", build=fails, env_factory=factory)
        job = mgr.start([make_spec(home=home)], install=False, request={"tiles": ["+43+005"]})
        assert job.wait(10.0) and job.status == "failed"
        (store,) = stores
        for _ in range(250):  # the job's thread ends just after its job
            if store() is None:
                break
            time.sleep(0.02)
        assert store() is None
        mgr.close()
    finally:
        gc.enable()
