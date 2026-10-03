# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The app's code opened once at the end of its Windows install (``orthostudio.warmup``): the first
start after an install took 10 s on a cloud PC against 3 s for the next ones, each new file being
read for the first time, from the disk and by the antivirus (a user, 2026-10-02)."""

from __future__ import annotations

from orthostudio import warmup


def test_every_module_but_the_commands() -> None:
    names = warmup.modules()
    assert "orthostudio.api.app" in names and "orthostudio.desktop" in names
    # a __main__ module runs its command as it is imported: OrthoStudio XP's engine
    assert not any(name.endswith(".__main__") for name in names)


def test_a_part_that_cannot_load_is_passed_over() -> None:
    """The setup goes on whatever happens: the app's own start says what is wrong."""
    opened: list[str] = []

    def load(name: str) -> None:
        opened.append(name)
        if name == "orthostudio.api.app":
            raise ImportError("a library Windows refused")

    assert warmup.main(load) == 0
    assert opened.index("orthostudio.api.app") < len(opened) - 1  # it went on
    assert opened[-1].startswith("webview")  # the window's parts come last
