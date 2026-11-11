# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's own loading window, over X-Plane while Live keeps it paused (``orthostudio.live.pause``).

While X-Plane is paused nothing of it runs, the plugin's loading window neither, so Live shows the
same window itself: the plugin's sizes, colours and words (``native/xplane_plugin/live_panel.m``,
``live_panel_win.c``, ``live_gate.c``'s ``show``), over the middle of X-Plane's window, above it
(full screen too). On a Mac a panel of the app (Cocoa, drawn on the app's main thread: there is
none when Live runs from a terminal, and no window then); on Windows a window of a thread of its
own, topmost, which never takes the keyboard; on Linux a process of its own while the window is up
(``pause_window_x11``: X11, without libX11 or a display no window, Live's log says the wait).

:func:`lines` gives the plugin's two lines in the Live window's language.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from orthostudio.fsutil import NO_CONSOLE_WINDOW

PANEL_W, PANEL_H, BAR_W, BAR_H = 560, 120, 400, 8

WORDS = {  # live_gate.c's show(), and "preparing the files" before the textures are counted
    "en": (
        "Loading the ortho around {}",
        "{} textures to build",
        "{:.1f} MB/s",
        "every photo in, building",
        "preparing the files",
        "{} night textures to build",
    ),
    "fr": (
        "Chargement de l'ortho autour de {}",
        "{} textures à construire",
        "{:.1f} Mo/s",
        "photos reçues, construction en cours",
        "préparation des fichiers",
        "{} textures de nuit à construire",
    ),
}


def lines(
    where: str,
    left: int,
    building: int,
    mbs: float,
    seconds: float,
    counted: bool,
    lang: str,
    night: int = 0,
) -> tuple[str, str]:
    """The window's headline and detail: ``where`` (the start's name in that language), the
    textures ``left`` (``building`` of them with every photo in), the download rate, the seconds
    since the start was known; ``counted``: False while the files of the squares are made, before
    the textures are counted."""
    around, to_build, rate, photos, preparing, nights = WORDS.get(lang, WORDS["en"])
    headline = around.format(where)
    if not counted:
        return headline, f"{preparing}  -  {seconds:.0f} s"
    if 0 < left <= night:  # the photos built: the night textures left (a start at night)
        return headline, f"{nights.format(left)}  -  {seconds:.0f} s"
    said = photos if left > 0 and building >= left else rate.format(mbs)
    if lang == "fr":
        said = said.replace(".", ",")
    return headline, f"{to_build.format(left)}  -  {said}  -  {seconds:.0f} s"


class PauseWindow:
    """``show(headline, detail, fraction)`` and ``hide()``, from any thread."""

    def __init__(self, xplane_pid: int | None = None) -> None:
        self._impl = None
        with contextlib.suppress(Exception):  # never in the way of the pause itself
            if sys.platform == "darwin":
                self._impl = _MacPanel()
            elif sys.platform == "win32":
                self._impl = _WinPanel(xplane_pid)
            else:
                self._impl = _X11Panel(xplane_pid)

    @property
    def shown_somewhere(self) -> bool:
        return self._impl is not None

    def show(self, headline: str, detail: str, fraction: float) -> None:
        if self._impl is not None:
            with contextlib.suppress(Exception):
                self._impl.show(headline, detail, max(0.0, min(1.0, fraction)))

    def hide(self) -> None:
        if self._impl is not None:
            with contextlib.suppress(Exception):
                self._impl.hide()


class _X11Panel:
    """``pause_window_x11``, a process that lasts as long as the window: each change one line of
    its standard input, whose end takes the window away (and Live gone, however it went)."""

    def __init__(self, xplane_pid: int | None) -> None:
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("no display: no window")
        self._pid = xplane_pid or 0
        self._child: subprocess.Popen | None = None
        self._gave_up = False  # it ended by itself: no libX11, no display to open
        self._lock = threading.Lock()

    def show(self, headline: str, detail: str, fraction: float) -> None:
        # short enough to go through the pipe in one piece, or not at all
        line = json.dumps([headline[:300], detail[:300], fraction]) + "\n"
        with self._lock:
            if self._child is None and not self._gave_up:
                self._child = self._start()
            if self._child is None:
                return
            if self._child.poll() is not None:
                self._child, self._gave_up = None, True
                return
            with contextlib.suppress(OSError):  # full (it will read the next one) or closed
                os.write(self._child.stdin.fileno(), line.encode())  # type: ignore[union-attr]

    def hide(self) -> None:
        with self._lock:
            child, self._child = self._child, None
        if child is not None:
            with contextlib.suppress(OSError):
                child.stdin.close()  # type: ignore[union-attr]
            with contextlib.suppress(subprocess.TimeoutExpired):
                child.wait(timeout=2)

    def _start(self) -> subprocess.Popen:
        here = Path(__file__)
        child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(here.with_name(f"pause_window_x11{here.suffix}")),
                str(self._pid),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # whatever stops Live's group, it sees its input end
            creationflags=NO_CONSOLE_WINDOW,  # X11 only: 0 there, as every launch says it
        )
        os.set_blocking(child.stdin.fileno(), False)  # type: ignore[union-attr]  # never waits
        return child


class _MacPanel:
    """An NSPanel like the plugin's, made and changed on the app's main thread."""

    def __init__(self) -> None:
        import AppKit
        from PyObjCTools import AppHelper

        app = AppKit.NSApp()
        if app is None or not app.isRunning():
            raise RuntimeError("no app running: no window")  # Live in a terminal
        self._appkit = AppKit
        self._helper = AppHelper
        self._panel = None
        self._labels = None
        self._fill = None

    def show(self, headline: str, detail: str, fraction: float) -> None:
        self._helper.callAfter(self._show, headline, detail, fraction)

    def hide(self) -> None:
        self._helper.callAfter(self._hide)

    def _show(self, headline: str, detail: str, fraction: float) -> None:
        import Quartz

        if self._panel is None:
            self._build()
        self._labels[0].setStringValue_(headline)
        self._labels[1].setStringValue_(detail)
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        self._fill.setFrame_(((0, 0), (BAR_W * fraction, BAR_H)))
        Quartz.CATransaction.commit()

    def _hide(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel = None  # placed over X-Plane's window again next time

    def _build(self) -> None:
        import Quartz

        ak = self._appkit
        around, layer = _xplane_frame_mac(ak, Quartz)
        r = ak.NSMakeRect(
            around.origin.x + around.size.width / 2 - PANEL_W / 2,
            around.origin.y + around.size.height / 2 - PANEL_H / 2,
            PANEL_W,
            PANEL_H,
        )
        p = ak.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            r,
            ak.NSWindowStyleMaskBorderless | ak.NSWindowStyleMaskNonactivatingPanel,
            ak.NSBackingStoreBuffered,
            False,
        )
        p.setLevel_(max(ak.NSStatusWindowLevel, layer + 1))
        p.setCollectionBehavior_(
            ak.NSWindowCollectionBehaviorCanJoinAllSpaces
            | ak.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        p.setOpaque_(False)
        p.setBackgroundColor_(ak.NSColor.clearColor())
        p.setHasShadow_(True)
        p.setReleasedWhenClosed_(False)
        box = ak.NSView.alloc().initWithFrame_(ak.NSMakeRect(0, 0, PANEL_W, PANEL_H))
        box.setWantsLayer_(True)
        box.layer().setBackgroundColor_(
            ak.NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).CGColor()
        )
        box.layer().setCornerRadius_(12)
        p.setContentView_(box)

        def label(y: float, h: float, size: float, white: float, text: str = ""):
            t = ak.NSTextField.labelWithString_(text)
            t.setFrame_(ak.NSMakeRect(20, y, PANEL_W - 40, h))
            t.setFont_(ak.NSFont.systemFontOfSize_(size))
            t.setTextColor_(ak.NSColor.colorWithCalibratedWhite_alpha_(white, 1.0))
            t.setAlignment_(ak.NSTextAlignmentCenter)
            t.setLineBreakMode_(ak.NSLineBreakByTruncatingTail)
            box.addSubview_(t)
            return t

        label(88, 20, 12, 0.6, "OrthoStudio XP Live")
        self._labels = (label(58, 26, 17, 1.0), label(36, 20, 13, 0.8))
        track = Quartz.CALayer.layer()
        track.setFrame_((((PANEL_W - BAR_W) / 2, 16), (BAR_W, BAR_H)))
        track.setBackgroundColor_(ak.NSColor.colorWithCalibratedWhite_alpha_(0.3, 1.0).CGColor())
        track.setCornerRadius_(BAR_H / 2)
        self._fill = Quartz.CALayer.layer()
        self._fill.setFrame_(((0, 0), (0, BAR_H)))
        self._fill.setBackgroundColor_(
            ak.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.25, 0.6, 1.0, 1.0).CGColor()
        )
        self._fill.setCornerRadius_(BAR_H / 2)
        track.addSublayer_(self._fill)
        box.layer().addSublayer_(track)
        p.orderFrontRegardless()
        self._panel = p


def _xplane_frame_mac(ak, quartz):
    """X-Plane's biggest window on screen in Cocoa's coordinates, and its layer; else the main
    screen. Its owner reads ``X-Plane 12.4.4-r1-797a4e10``: the start of the name is enough."""
    best = None
    infos = quartz.CGWindowListCopyWindowInfo(
        quartz.kCGWindowListOptionOnScreenOnly, quartz.kCGNullWindowID
    )
    for w in infos or []:
        if not str(w.get("kCGWindowOwnerName") or "").startswith("X-Plane"):
            continue
        b = w["kCGWindowBounds"]
        if best is None or b["Width"] * b["Height"] > best[0]["Width"] * best[0]["Height"]:
            best = (b, int(w.get("kCGWindowLayer", 0)))
    if best is None:
        return ak.NSScreen.mainScreen().frame(), 0
    b, layer = best
    top = ak.NSScreen.screens()[0].frame().size.height
    return ak.NSMakeRect(b["X"], top - b["Y"] - b["Height"], b["Width"], b["Height"]), layer


class _WinPanel:
    """A topmost window of a thread of its own, drawn as live_panel_win.c draws the plugin's."""

    WM_SYNC = 0x8000 + 1  # WM_APP + 1

    def __init__(self, xplane_pid: int | None) -> None:
        self._pid = xplane_pid
        self._want: tuple[str, str, float] | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread_id = 0
        threading.Thread(target=self._run, name="live-pause-window", daemon=True).start()
        self._ready.wait(5)

    def show(self, headline: str, detail: str, fraction: float) -> None:
        with self._lock:
            self._want = (headline, detail, fraction)
        self._wake()

    def hide(self) -> None:
        with self._lock:
            self._want = None
        self._wake()

    def _wake(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_SYNC, 0, 0)  # type: ignore[attr-defined]

    def _run(self) -> None:  # pragma: no cover - Windows only
        from ctypes import wintypes

        user32, gdi32, k32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32  # type: ignore[attr-defined]
        with contextlib.suppress(AttributeError):
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # per monitor v2: sharp text
        lresult = ctypes.c_ssize_t
        wndproc = ctypes.WINFUNCTYPE(
            lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = lresult
        user32.CreateWindowExW.restype = wintypes.HWND
        state: dict = {"hwnd": None, "shown": None, "scale": 1.0}

        def font(px: int):
            return gdi32.CreateFontW(
                -int(px * state["scale"]), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI"
            )

        def paint(hwnd, dc) -> None:
            s = state["scale"]
            w, h = int(PANEL_W * s), int(PANEL_H * s)
            rect = wintypes.RECT(0, 0, w, h)
            back = gdi32.CreateSolidBrush(0x141414)
            user32.FillRect(dc, ctypes.byref(rect), back)
            gdi32.DeleteObject(back)
            gdi32.SetBkMode(dc, 1)  # TRANSPARENT
            shown = state["shown"]
            if shown is None:
                return
            headline, detail, fraction = shown
            for text, top, height, px, grey in (
                ("OrthoStudio XP Live", 12, 20, 12, 0x999999),
                (headline, 36, 26, 17, 0xFFFFFF),
                (detail, 64, 20, 13, 0xCCCCCC),
            ):
                f = font(px)
                old = gdi32.SelectObject(dc, f)
                gdi32.SetTextColor(dc, grey)
                r = wintypes.RECT(
                    int(20 * s), int(top * s), int((PANEL_W - 20) * s), int((top + height) * s)
                )
                user32.DrawTextW(
                    dc, text, -1, ctypes.byref(r), 0x1 | 0x20 | 0x8000
                )  # CENTER SINGLELINE END_ELLIPSIS
                gdi32.SelectObject(dc, old)
                gdi32.DeleteObject(f)
            x0, y0 = int((PANEL_W - BAR_W) / 2 * s), int((PANEL_H - 16 - BAR_H) * s)
            for colour, width in ((0x4D4D4D, BAR_W), (0xFF9940, BAR_W * fraction)):  # BGR
                brush = gdi32.CreateSolidBrush(colour)
                r = wintypes.RECT(x0, y0, x0 + int(width * s), y0 + int(BAR_H * s))
                user32.FillRect(dc, ctypes.byref(r), brush)
                gdi32.DeleteObject(brush)

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wintypes.HDC),
                ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", ctypes.c_byte * 32),
            ]

        @wndproc
        def proc(hwnd, msg, wp, lp):
            if msg == 0x0014:  # WM_ERASEBKGND: the whole window is drawn at once, below
                return 1
            if msg == 0x000F:  # WM_PAINT
                ps = PAINTSTRUCT()
                dc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                # drawn apart, then put up in one go: Windows composes the window as it is drawn,
                # and showed it now and then with the background alone (Shadow, 2026-11-09)
                w, h = int(PANEL_W * state["scale"]), int(PANEL_H * state["scale"])
                apart = gdi32.CreateCompatibleDC(dc)
                bitmap = gdi32.CreateCompatibleBitmap(dc, w, h)
                before = gdi32.SelectObject(apart, bitmap)
                paint(hwnd, apart)
                gdi32.BitBlt(dc, 0, 0, w, h, apart, 0, 0, 0x00CC0020)  # SRCCOPY
                gdi32.SelectObject(apart, before)
                gdi32.DeleteObject(bitmap)
                gdi32.DeleteDC(apart)
                user32.EndPaint(hwnd, ctypes.byref(ps))
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        # the module's handle whole: an address, beyond 32 bits, which ctypes cut to an int
        # (« Windows fatal exception: access violation » at RegisterClassW, the Shadow's tests,
        # 2026-11-11); passed back as a handle, not an int ctypes would cut again
        k32.GetModuleHandleW.restype = wintypes.HMODULE
        k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        module = wintypes.HINSTANCE(k32.GetModuleHandleW(None))
        wc = WNDCLASSW(0, proc, 0, 0, module, None, None, None, None, "OrthoStudioXPLivePause")
        user32.RegisterClassW(ctypes.byref(wc))
        self._thread_id = k32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # the thread's queue exists now
        self._ready.set()
        user32.SetTimer(None, 0, 1000, None)  # back on top every second, as the plugin's

        def sync() -> None:
            with self._lock:
                want = self._want
            hwnd = state["hwnd"]
            if want is None:
                if hwnd:
                    user32.DestroyWindow(hwnd)
                state["hwnd"] = state["shown"] = None
                return
            changed = want != state["shown"]
            state["shown"] = want
            if not hwnd:
                x, y, scale = _xplane_middle_win(user32, self._pid)
                state["scale"] = scale
                w, h = int(PANEL_W * scale), int(PANEL_H * scale)
                hwnd = user32.CreateWindowExW(
                    0x8 | 0x80 | 0x08000000 | 0x80000,  # TOPMOST TOOLWINDOW NOACTIVATE LAYERED
                    "OrthoStudioXPLivePause",
                    "OrthoStudio XP Live",
                    0x80000000,  # WS_POPUP
                    x - w // 2,
                    y - h // 2,
                    w,
                    h,
                    None,
                    None,
                    module,
                    None,
                )
                user32.SetLayeredWindowAttributes(hwnd, 0, 235, 0x2)  # LWA_ALPHA, the Mac's 0.92
                radius = int(24 * scale)
                user32.SetWindowRgn(
                    hwnd, gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius, radius), False
                )
                user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
                user32.SetWindowPos(
                    hwnd, -1, 0, 0, 0, 0, 0x2 | 0x1 | 0x10 | 0x200
                )  # TOPMOST, no move/size/activate
                state["hwnd"] = hwnd
            elif changed:
                user32.InvalidateRect(hwnd, None, False)

        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_SYNC:
                sync()
            elif msg.message == 0x0113 and state["hwnd"]:  # WM_TIMER
                user32.SetWindowPos(state["hwnd"], -1, 0, 0, 0, 0, 0x2 | 0x1 | 0x10 | 0x200)
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


def _xplane_middle_win(user32, pid: int | None) -> tuple[int, int, float]:  # pragma: no cover
    """The middle of X-Plane's biggest visible window and its scale (its DPI / 96); else the
    primary screen's."""
    from ctypes import wintypes

    best: list = []
    if pid:
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @enum_proc
        def each(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                area = (r.right - r.left) * (r.bottom - r.top)
                if not best or area > best[0]:
                    best[:] = [area, hwnd, r]
            return True

        user32.EnumWindows(each, 0)
    if best:
        _, hwnd, r = best
        dpi = 96
        with contextlib.suppress(AttributeError):
            dpi = user32.GetDpiForWindow(hwnd) or 96
        return (r.left + r.right) // 2, (r.top + r.bottom) // 2, dpi / 96
    return user32.GetSystemMetrics(0) // 2, user32.GetSystemMetrics(1) // 2, 1.0
