# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""``LogLive.txt``: the Live window's log in a file, beside X-Plane's ``Log.txt``
(``orthostudio.live.logfile``)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from orthostudio.live import logfile
from orthostudio.live.logfile import KEEP, NAME, LogFile, set_aside, started

HEADER = "LogLive.txt for OrthoStudio XP Live 0.1.18 (code 0)\nLive started on {}\n\n"


def test_a_log_as_xplane_s(tmp_path: Path) -> None:
    """A header, then each line with its time, in the file as it comes."""
    path = tmp_path / "X-Plane 12" / NAME
    log = LogFile(path, archive=tmp_path / "live" / "log")
    assert log.open
    log.write("streaming into the pack")
    log.write("two\nlines")
    lines = path.read_text(encoding="utf-8").splitlines()  # before any close: flushed
    assert lines[0].startswith("LogLive.txt for OrthoStudio XP Live ")
    assert lines[1].startswith("Live started on ")
    assert lines[2].startswith("This computer: ")
    assert lines[3] == ""
    assert [line[9:] for line in lines[4:]] == ["streaming into the pack", "two", "lines"]
    assert all(time.strptime(line[:8], "%H:%M:%S") for line in lines[4:])
    log.close()
    log.write("after")  # closed: nothing, and no error
    assert path.read_text(encoding="utf-8").splitlines()[-1].endswith("lines")


def test_the_run_before_goes_to_the_archive(tmp_path: Path) -> None:
    path, archive = tmp_path / NAME, tmp_path / "live" / "log"
    path.write_text(HEADER.format("Tue Sep 29 10:44:16 2026") + "10:44:17 the old run\n")
    LogFile(path, archive=archive).close()
    kept = archive / "LogLive 2026-09-29 10-44-16.txt"
    assert [p.name for p in archive.iterdir()] == [kept.name]  # named for when it started
    assert kept.read_text().endswith("the old run\n")
    assert "the old run" not in path.read_text()  # a new log
    path.write_text(HEADER.format("Tue Sep 29 10:44:16 2026"))  # the same start again
    set_aside(path, archive)
    assert sorted(p.name for p in archive.iterdir()) == [
        "LogLive 2026-09-29 10-44-16 (2).txt",
        kept.name,
    ]


def test_only_the_last_runs_are_kept(tmp_path: Path) -> None:
    archive = tmp_path / "log"
    archive.mkdir()
    for day in range(1, 13):
        (archive / f"LogLive 2026-09-{day:02d} 10-00-00.txt").write_text("old")
    path = tmp_path / NAME
    path.write_text(HEADER.format("Tue Sep 29 10:44:16 2026"))
    set_aside(path, archive)
    names = sorted(p.name for p in archive.iterdir())
    assert len(names) == KEEP
    assert names[0] == "LogLive 2026-09-04 10-00-00.txt"  # the three oldest gone
    assert names[-1] == "LogLive 2026-09-29 10-44-16.txt"


def test_when_a_run_started(tmp_path: Path) -> None:
    """Its header's line, whatever language the system speaks, else the file's last change."""
    path = tmp_path / NAME
    path.write_text(HEADER.format("Wed Sep  2 08:05:09 2026"))  # time.ctime's two spaces
    assert time.localtime(started(path))[:6] == (2026, 9, 2, 8, 5, 9)
    path.write_text("no header\n")
    os.utime(path, (1_790_000_000, 1_790_000_000))
    assert started(path) == 1_790_000_000


def test_a_log_that_cannot_be_written_stops_nothing(tmp_path: Path) -> None:
    (tmp_path / "a file").write_text("")
    log = LogFile(tmp_path / "a file" / NAME)  # a file where its folder should be
    assert not log.open
    log.write("nothing breaks")
    log.echo("nor here")
    log.close()


def test_where_the_log_goes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Beside X-Plane's Log.txt; else, and for a test window (``OSXP_HOME``), in Live's folder."""
    xplane, live = tmp_path / "X-Plane 12", tmp_path / "live"
    xplane.mkdir()
    monkeypatch.setattr("orthostudio.live.pack.live_root", lambda: live)
    monkeypatch.setattr("orthostudio.install.xplane.detect_xplane", lambda: xplane)
    monkeypatch.delenv("OSXP_HOME", raising=False)
    assert logfile.log_path() == xplane / NAME
    assert logfile.archive_folder() == live / "log"
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))  # never the user's X-Plane
    assert logfile.log_path() == live / NAME
    monkeypatch.delenv("OSXP_HOME")
    monkeypatch.setattr("orthostudio.install.xplane.detect_xplane", lambda: None)  # no X-Plane
    assert logfile.log_path() == live / NAME


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permissions")
def test_an_xplane_folder_live_cannot_write_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xplane, live = tmp_path / "X-Plane 12", tmp_path / "live"
    xplane.mkdir()
    xplane.chmod(0o555)
    try:
        monkeypatch.setattr("orthostudio.live.pack.live_root", lambda: live)
        monkeypatch.setattr("orthostudio.install.xplane.detect_xplane", lambda: xplane)
        monkeypatch.delenv("OSXP_HOME", raising=False)
        if os.access(xplane, os.W_OK):  # root may write anywhere
            pytest.skip("this user writes in a read-only folder")
        assert logfile.log_path() == live / NAME
    finally:
        xplane.chmod(0o755)
