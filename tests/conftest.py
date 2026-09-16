"""Shared pytest configuration.

The suite tests OrthoStudio XP on its own: no test reads, runs or compares with Ortho4XP
(decision 0010). Tests marked ``xplane`` read the local X-Plane 12 install, never write to it,
and skip when it is absent; tests marked ``network`` talk to real servers.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# OrthoStudio XP's builds default to X-Plane 12's relief (decision 0007), which needs a Global
# Scenery to read. The suite builds with the viewfinderpanoramas relief instead, unless a test
# asks for X-Plane's explicitly (``BuildSpec(relief="xplane")``, ``tests/test_dem_xplane.py``).
os.environ.setdefault("OSXP_RELIEF", "view")


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


@pytest.fixture(params=[False, True], ids=["fromstring", "split-tokens"])
def number_parsing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    """Both ways numbers are read from text (``orthostudio.numtext``): ``np.fromstring``, and the
    split tokens of Windows, whose C library parses numbers slowly."""
    from orthostudio import numtext

    monkeypatch.setattr(numtext, "SPLIT_TOKENS", request.param)
    return bool(request.param)


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
