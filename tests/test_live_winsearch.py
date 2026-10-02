# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's folder kept out of Windows Search (orthostudio.live.winsearch): the rule's URLs on any
system; on Windows itself, a folder of the user's that the indexer crawls taken out by the rule,
then let in again, so that the test leaves nothing behind."""

import os
import shutil
import sys
import uuid
from pathlib import Path, PureWindowsPath

import pytest

from orthostudio.live import winsearch


def test_the_rule_s_urls_as_the_indexer_writes_them():
    folder = PureWindowsPath(r"C:\Users\Shadow\Desktop\Vols de test é\live")
    assert (
        winsearch.folder_url(folder) == "file:///C:\\Users\\Shadow\\Desktop\\Vols de test é\\live\\"
    )
    assert winsearch.rule_url(folder) == winsearch.folder_url(folder) + "*"
    assert winsearch.folder_url(PureWindowsPath("D:\\Live\\")) == "file:///D:\\Live\\"


def test_nothing_elsewhere_than_on_windows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(winsearch, "WINDOWS", False)
    called = []
    monkeypatch.setattr(winsearch, "_with_scope", lambda work: called.append(work))
    assert winsearch.keep_out(Path("live")) is False
    assert winsearch.included(Path("live")) is None
    assert called == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Search")
def test_a_crawled_folder_kept_out_then_let_in():
    # under the user's own folder, which Windows Search crawls (a temporary folder is under
    # AppData, which it does not)
    folder = Path(os.environ["USERPROFILE"]) / f"osxp-live-search-test-{uuid.uuid4().hex[:8]}"
    (folder / "terrain").mkdir(parents=True)
    said: list[str] = []
    try:
        if winsearch.included(folder) is not True:
            pytest.skip("Windows Search is off, or does not crawl the user's folder here")
        assert winsearch.keep_out(folder, said.append) is True
        assert winsearch.included(folder) is False
        assert winsearch.included(folder / "terrain") is False
        assert "kept out of Windows Search" in said[0]
        assert winsearch.keep_out(folder, said.append) is False  # once
        assert len(said) == 1
    finally:
        winsearch.let_in(folder)
        shutil.rmtree(folder, ignore_errors=True)
    assert winsearch.included(folder) is True
