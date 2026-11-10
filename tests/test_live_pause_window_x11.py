# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's loading window over a paused X-Plane on Linux: the process that shows it, what Live says
to it, and, where an X server can be started, the window itself over a stand-in for X-Plane."""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import POINTER, byref, c_char_p, c_int, c_uint, c_ulong, c_void_p
from pathlib import Path
from unittest import mock

import orthostudio.live.pause_window as pause_window
from orthostudio.live import pause_window_x11 as x11

SCRIPT = str(Path(x11.__file__))


def until(condition, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


@unittest.skipIf(sys.platform == "win32", "X11's types on a 64-bit Unix")
class LayoutTest(unittest.TestCase):
    """What Xlib, Xft and XRandR's headers give, on a 64-bit system: a field out of place would
    read or write beside the structure, with nothing to say so."""

    def test_structures_are_the_size_of_the_headers(self) -> None:
        for structure, size in (
            (x11.XSetWindowAttributes, 112),
            (x11.XWindowAttributes, 136),
            (x11.XColor, 16),
            (x11.XEvent, 192),
            (x11.XFontStruct, 96),
            (x11.XftColor, 16),
            (x11.XftFont, 32),
            (x11.XGlyphInfo, 12),
            (x11.XRRMonitorInfo, 56),
        ):
            self.assertEqual(ctypes.sizeof(structure), size, structure.__name__)

    def test_a_fixed_font_shows_what_it_lacks_as_a_question_mark(self) -> None:
        self.assertEqual(x11.chars16("é", narrow=True), b"\x00\xe9")
        self.assertEqual(x11.chars16("€", narrow=True), b"\x00?")
        self.assertEqual(x11.chars16("€", narrow=False), b"\x20\xac")
        self.assertEqual(x11.chars16("😀", narrow=False), b"\x00?")


@unittest.skipIf(sys.platform == "win32", "the Linux window")
class ProcessTest(unittest.TestCase):
    def test_without_a_display_it_ends_at_once(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        done = subprocess.run(
            [sys.executable, "-I", SCRIPT, "1"],
            input=b"",
            env=env,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(done.returncode, 0, done.stderr)


@unittest.skipIf(sys.platform == "win32", "the Linux window")
class WhatLiveSaysTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.heard = Path(tmp.name) / "heard"
        self.started = 0

    def stand_in(self, code: str):
        """``_X11Panel`` with a stand-in for the window's process; DISPLAY set for it."""

        def start(panel) -> subprocess.Popen:
            self.started += 1
            child = subprocess.Popen(
                [sys.executable, "-I", "-c", code, str(self.heard)], stdin=subprocess.PIPE
            )
            os.set_blocking(child.stdin.fileno(), False)
            return child

        env = mock.patch.dict(os.environ, {"DISPLAY": ":99"})
        env.start()
        self.addCleanup(env.stop)
        patch = mock.patch.object(pause_window._X11Panel, "_start", start)
        patch.start()
        self.addCleanup(patch.stop)
        return pause_window._X11Panel(1234)

    def test_each_change_is_a_line_and_hide_ends_the_input(self) -> None:
        panel = self.stand_in(
            "import sys; d = sys.stdin.buffer.read(); open(sys.argv[1], 'wb').write(d)"
        )
        panel.show("Chargement de l'ortho autour de Genève", "12 textures", 0.25)
        panel.show("x" * 1000, "y" * 1000, 0.5)
        panel.hide()
        said = [json.loads(line) for line in self.heard.read_text().splitlines()]
        self.assertEqual(said[0], ["Chargement de l'ortho autour de Genève", "12 textures", 0.25])
        self.assertEqual(said[1], ["x" * 300, "y" * 300, 0.5])
        self.assertEqual(self.started, 1)

    def test_shown_again_after_hide_it_starts_again(self) -> None:
        panel = self.stand_in("import sys; sys.stdin.buffer.read()")
        panel.show("a", "b", 0.0)
        panel.hide()
        panel.show("a", "b", 0.0)
        panel.hide()
        self.assertEqual(self.started, 2)

    def test_a_process_that_ended_by_itself_is_not_started_again(self) -> None:
        panel = self.stand_in("pass")  # what it does without libX11
        panel.show("a", "b", 0.0)
        self.assertTrue(until(lambda: panel._child.poll() is not None))
        for _ in range(5):
            panel.show("a", "b", 0.0)
        panel.hide()
        self.assertEqual(self.started, 1)


@unittest.skipUnless(sys.platform == "linux", "Linux's choice of window")
class NoDisplayTest(unittest.TestCase):
    def test_without_a_display_no_window_and_nothing_in_the_way(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        with mock.patch.dict(os.environ, env, clear=True):
            window = pause_window.PauseWindow(1)
        self.assertFalse(window.shown_somewhere)
        window.show("a", "b", 0.5)
        window.hide()


class XPlaneStandIn:
    """A window of this process listed by the window manager, answering pings, as X-Plane's."""

    def __init__(self) -> None:
        self.x = x11.bound("libX11.so.6", x11.X11)
        self.x.XMapWindow.argtypes = [c_void_p, c_ulong]
        self.x.XQueryTree.argtypes = [
            c_void_p,
            c_ulong,
            POINTER(c_ulong),
            POINTER(c_ulong),
            POINTER(POINTER(c_ulong)),
            POINTER(c_uint),
        ]
        self.dpy = self.x.XOpenDisplay(None)
        self.root = self.x.XDefaultRootWindow(self.dpy)
        a = x11.XSetWindowAttributes()
        self.win = self.x.XCreateWindow(
            self.dpy, self.root, 100, 60, 1200, 800, 0, 0, 1, None, 0, byref(a)
        )
        self.protocols = self.atom(b"WM_PROTOCOLS")
        self.ping = self.atom(b"_NET_WM_PING")
        self.delete = self.atom(b"WM_DELETE_WINDOW")
        self.set(self.win, self.protocols, x11.XA_ATOM, [self.delete, self.ping])
        self.set(self.win, self.atom(b"_NET_WM_PID"), x11.XA_CARDINAL, [os.getpid()])
        self.set(self.root, self.atom(b"_NET_CLIENT_LIST"), x11.XA_WINDOW, [self.win])
        self.x.XMapWindow(self.dpy, self.win)
        self.x.XSync(self.dpy, 0)

    def atom(self, name: bytes) -> int:
        return self.x.XInternAtom(self.dpy, c_char_p(name), 0)

    def set(self, window: int, name: int, kind: int, values: list[int]) -> None:
        data = (c_ulong * len(values))(*values)
        self.x.XChangeProperty(self.dpy, window, name, kind, 32, 0, data, len(values))

    def said(self) -> list[int]:
        got, form, n, left = c_ulong(), c_int(), c_ulong(), c_ulong()
        value = POINTER(c_ulong)()
        self.x.XSync(self.dpy, 0)
        self.x.XGetWindowProperty(
            self.dpy,
            self.win,
            self.protocols,
            0,
            16,
            0,
            x11.XA_ATOM,
            byref(got),
            byref(form),
            byref(n),
            byref(left),
            byref(value),
        )
        out = [value[i] for i in range(n.value)]
        self.x.XFree(value)
        return out

    def others(self) -> list[tuple[int, int, int, int]]:
        """The other top windows: x, y, width, height."""
        root, parent, children = c_ulong(), c_ulong(), POINTER(c_ulong)()
        n = c_uint()
        self.x.XSync(self.dpy, 0)
        self.x.XQueryTree(
            self.dpy, self.root, byref(root), byref(parent), byref(children), byref(n)
        )
        out = []
        for i in range(n.value):
            if children[i] != self.win:
                a = x11.XWindowAttributes()
                self.x.XGetWindowAttributes(self.dpy, children[i], byref(a))
                out.append((a.x, a.y, a.width, a.height))
        if n.value:
            self.x.XFree(children)
        return out

    def close(self) -> None:
        self.x.XCloseDisplay(self.dpy)


@unittest.skipUnless(
    sys.platform == "linux" and shutil.which("Xvfb"), "an X server to start: Xvfb on Linux"
)
class WindowOverXPlaneTest(unittest.TestCase):
    def setUp(self) -> None:
        read, write = os.pipe()
        self.server = subprocess.Popen(
            ["Xvfb", "-displayfd", str(write), "-screen", "0", "1600x1000x24", "-nolisten", "tcp"],
            pass_fds=[write],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(write)
        self.addCleanup(self.server.wait)
        self.addCleanup(self.server.terminate)
        number = os.read(read, 16).strip().decode()
        os.close(read)
        env = mock.patch.dict(os.environ, {"DISPLAY": f":{number}"})
        env.start()
        self.addCleanup(env.stop)
        self.xplane = XPlaneStandIn()
        self.addCleanup(self.xplane.close)

    def window(self) -> pause_window.PauseWindow:
        window = pause_window.PauseWindow(os.getpid())
        self.addCleanup(window.hide)
        self.assertTrue(window.shown_somewhere)
        window.show("Chargement de l'ortho autour de Genève", "12 textures à construire", 0.4)
        return window

    def test_over_the_middle_of_xplane_which_stops_answering_pings_meanwhile(self) -> None:
        window = self.window()
        x = self.xplane
        self.assertTrue(until(lambda: x.ping not in x.said()), "pings still answered")
        self.assertEqual(x.said(), [x.delete])
        self.assertEqual(x.others(), [(100 + 600 - 280, 60 + 400 - 60, 560, 120)])
        window.hide()
        self.assertEqual(x.said(), [x.delete, x.ping])
        self.assertEqual(x.others(), [])

    def test_live_gone_however_it_went_gives_everything_back(self) -> None:
        window = self.window()
        x = self.xplane
        self.assertTrue(until(lambda: x.ping not in x.said()))
        child = window._impl._child
        child.stdin.close()  # what the end of Live does to the pipe
        self.assertEqual(child.wait(timeout=10), 0)
        self.assertEqual(x.said(), [x.delete, x.ping])
        self.assertEqual(x.others(), [])

    def test_xplane_gone_while_the_window_is_up_ends_quietly(self) -> None:
        window = self.window()
        x = self.xplane
        self.assertTrue(until(lambda: x.ping not in x.said()))
        x.x.XDestroyWindow(x.dpy, x.win)
        x.x.XSync(x.dpy, 0)
        child = window._impl._child
        window.hide()
        self.assertEqual(child.returncode, 0)


if __name__ == "__main__":
    unittest.main()
