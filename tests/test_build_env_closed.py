# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The store's index is let go after each build and each plan.

Found on the owner's Mac (2026-10-06): the data folder moved to another disk in Settings, the old
one could not be ejected while the app ran ("dissented by" its Python): every build and every plan
had opened the store's index through its build environment, and only Python collecting the
environment closed it. The environments here are the real ones, on a fake X-Plane.
"""

from __future__ import annotations

import sqlite3
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
