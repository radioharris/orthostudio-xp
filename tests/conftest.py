# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Shared pytest configuration.

The suite tests OrthoStudio XP on its own: no test reads, runs or compares with Ortho4XP
(decision 0010). Tests marked ``xplane`` read the local X-Plane 12 install, never write to it,
and skip when it is absent; tests marked ``network`` talk to real servers; tests marked
``serial`` wait on their own timing and are left out of a parallel run (``-n``), run alone with
``-m serial``.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# OrthoStudio XP's builds default to X-Plane 12's relief (decision 0007), which needs a Global
# Scenery to read. The suite builds with the viewfinderpanoramas relief instead, unless a test
# asks for X-Plane's explicitly (``BuildSpec(relief="xplane")``, ``tests/test_dem_xplane.py``).
os.environ.setdefault("OSXP_RELIEF", "view")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """The ``serial`` tests left out of a parallel run: two start tests of test_live_gate failed
    under its load and passed alone (2026-11-11); ``pytest -m serial`` runs them, as the CI does
    after its parallel run."""
    if not (os.environ.get("PYTEST_XDIST_WORKER") or getattr(config.option, "numprocesses", None)):
        return
    left = [item for item in items if item.get_closest_marker("serial")]
    if left:
        config.hook.pytest_deselected(items=left)
        items[:] = [item for item in items if not item.get_closest_marker("serial")]


@pytest.fixture(autouse=True)
def _no_sources_of_this_machine(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The imagery sources a user added live in ``$OSXP_HOME/sources.toml``: a test that does not
    set ``$OSXP_HOME`` never reads those of the machine it runs on."""
    from orthostudio.home import OSXP_HOME_ENV
    from orthostudio.imagery import providers

    nowhere = tmp_path_factory.getbasetemp() / "no-home" / providers.USER_SOURCES_FILE
    real = providers.user_sources_path

    def path() -> Path:
        return real() if os.environ.get(OSXP_HOME_ENV) else nowhere

    monkeypatch.setattr(providers, "user_sources_path", path)


@pytest.fixture(autouse=True)
def _no_xplane_unless_a_test_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """No process of the machine counts as X-Plane: a stand-in that another test of the run has
    started on Windows stopped a delete with XP_RUNNING (2026-11-09). A test about it sets its
    own."""
    from orthostudio.install import packs

    monkeypatch.setattr(packs, "xplane_running", lambda: False)


@pytest.fixture(autouse=True)
def _live_off_unless_a_test_switches_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """OrthoStudio XP Live is on by default
    (``orthostudio.live.ENABLED_BY_DEFAULT``): the suite keeps it off unless a test switches it on
    (``enabled = true`` in its ``live.toml``), as before, so that a test's app or OrthoStudio XP's
    engine never reaches for a Live window of the machine it runs on (port 8642)."""
    import orthostudio.live as live

    monkeypatch.setattr(live, "ENABLED_BY_DEFAULT", False)


@pytest.fixture(autouse=True)
def _no_frame_unless_a_test_asks_for_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app opens in its frame where the system gives a window (``orthostudio.live.frame``): the
    suite keeps it off unless a test asks for it, so that a test of the app's start never shows a
    real window on the screen of the machine it runs on (one did, 2026-10-04)."""
    from orthostudio.live.frame import OFF_ENV

    monkeypatch.setenv(OFF_ENV, "0")


@pytest.fixture(params=[False, True], ids=["fromstring", "split-tokens"])
def number_parsing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    """Both ways numbers are read from text (``orthostudio.numtext``): ``np.fromstring``, and the
    split tokens of Windows, whose C library parses numbers slowly."""
    from orthostudio import numtext

    monkeypatch.setattr(numtext, "SPLIT_TOKENS", request.param)
    return bool(request.param)


@pytest.fixture
def view_site_at_3sec(monkeypatch: pytest.MonkeyPatch) -> None:
    """viewfinderpanoramas as a site of 3" blocks only: for tests whose ``.hgt`` files are 3" and
    must be reused as they are. The real list (``data/viewfinder.json``) serves 1" over much of
    Europe and North America since March 2026, where a 3" file is no longer enough and the 1"
    block is asked."""
    from orthostudio.dem import sources

    dem1, dem3, _ = sources.site_archives()
    every = frozenset(dem1) | frozenset(dem3)
    monkeypatch.setattr(sources, "site_archives", lambda: (frozenset(), every, {}))


@pytest.fixture
def no_symlink_privilege(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installs get what an account without Developer Mode gets on Windows: the symbolic link
    refused (WinError 1314), so a junction. Most Windows users; never the administrator account of
    a CI runner."""
    from orthostudio.install import packs

    def refused(pack_dir: object, target: object) -> None:
        raise OSError(22, "A required privilege is not held by the client", None, 1314)

    monkeypatch.setattr(packs, "_symlink", refused)


@pytest.fixture(
    params=[
        "symlink",
        pytest.param(
            "junction", marks=pytest.mark.skipif(os.name != "nt", reason="a junction is Windows'")
        ),
    ]
)
def link_kind(request: pytest.FixtureRequest) -> str:
    """How an install puts a pack into Custom Scenery: a symbolic link, and on Windows a junction
    too (:func:`no_symlink_privilege`)."""
    if request.param == "junction":
        request.getfixturevalue("no_symlink_privilege")
    return str(request.param)


@pytest.fixture(autouse=True)
def _no_xplane_folder_of_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The X-Plane folder a user named (``essential.xplane_dir``) is read from
    ``$OSXP_HOME/config.toml``: a test that does not set ``$OSXP_HOME`` never reads that of the
    machine it runs on."""
    from orthostudio import home
    from orthostudio.config.models import Settings
    from orthostudio.live import scenery

    real = scenery.load_settings

    def settings() -> Settings:
        return real() if os.environ.get(home.OSXP_HOME_ENV) else Settings()

    monkeypatch.setattr(scenery, "load_settings", settings)


@pytest.fixture(autouse=True)
def _no_data_folder_of_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The data folder a user chose (``essential.data_dir``, an external disk) is read from
    ``$OSXP_HOME/config.toml``: a test that does not set ``$OSXP_HOME`` never writes there, and
    ``$OSXP_DATA_DIR`` set in the shell that runs the suite is ignored."""
    from orthostudio import home

    monkeypatch.delenv(home.DATA_DIR_ENV, raising=False)
    real = home._configured_data_dir

    def configured() -> Path | None:
        return real() if os.environ.get(home.OSXP_HOME_ENV) else None

    monkeypatch.setattr(home, "_configured_data_dir", configured)


@pytest.fixture(autouse=True)
def _no_pack_sizes_left_measuring() -> Iterator[None]:
    """The Library measures and checks its tiles' sizes in a thread of its own
    (``api.app._measure_later``): a test leaves none running into the next one, whose own
    ``_pack_bytes`` it would call."""
    yield
    appmod = sys.modules.get("orthostudio.api.app")
    deadline = time.monotonic() + 10
    while appmod is not None and appmod._PACK_SIZES_MEASURING and time.monotonic() < deadline:
        time.sleep(0.01)
