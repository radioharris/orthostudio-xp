# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's loading window over a paused X-Plane on Linux, a process of its own.

The window ``orthostudio.live.pause_window`` asks for, drawn as the plugin's is on Linux
(``native/xplane_plugin/live_panel_x11.c``: its sizes, colours, fonts and the way it finds X-Plane's
window), with X11 through ctypes. Run as a script, by path and with nothing of OrthoStudio XP
imported, so that it starts at once; apart from Live because Live's own window is GTK in the same
process, and Xlib's error handler is one for a whole process.

Its standard input brings one JSON line per change, ``[headline, detail, fraction]``. At the end of
that input, Live done with the window or gone however it went, the window goes and X-Plane's windows
are given back what they said. Without libX11 or a display it ends at once: X-Plane waits all the
same.

While X-Plane is paused it cannot answer the window manager's pings, and GNOME's would offer after
5 s to force it to quit. So for the window's time X-Plane's windows stop saying they answer pings
(``_NET_WM_PING`` out of their ``WM_PROTOCOLS``), as the plugin does at a start.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import select
import sys
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    Union,
    byref,
    c_char_p,
    c_int,
    c_long,
    c_short,
    c_uint,
    c_ulong,
    c_ushort,
    c_void_p,
)

PANEL_W, PANEL_H, BAR_W, BAR_H = 560, 120, 480, 8  # live_panel_x11.c's
RAISE_S = 1.0  # back on top this often, as the plugin's

EXPOSE = 12
EXPOSURE_MASK = 1 << 15
CW_BACK_PIXEL, CW_BORDER_PIXEL, CW_OVERRIDE_REDIRECT, CW_EVENT_MASK = (
    1 << 1,
    1 << 3,
    1 << 9,
    1 << 11,
)
INPUT_OUTPUT, IS_VIEWABLE = 1, 2
XA_ATOM, XA_CARDINAL, XA_WINDOW = 4, 6, 33

BG, TITLE, WHITE, DETAIL, TRACK, FILL = range(6)
RGB = (
    (20, 20, 20),
    (153, 153, 153),
    (255, 255, 255),
    (204, 204, 204),
    (77, 77, 77),
    (64, 153, 255),
)
TITLE_F, HEAD_F, DETAIL_F = range(3)
FONT_PX = (12, 17, 13)
FIXED_FONTS = (
    b"-misc-fixed-medium-r-normal--13-*-*-*-*-*-iso10646-1",
    b"-misc-fixed-medium-r-normal--18-*-*-*-*-*-iso10646-1",
    b"-misc-fixed-medium-r-normal--13-*-*-*-*-*-iso10646-1",
)


class XSetWindowAttributes(Structure):
    _fields_ = [
        ("background_pixmap", c_ulong),
        ("background_pixel", c_ulong),
        ("border_pixmap", c_ulong),
        ("border_pixel", c_ulong),
        ("bit_gravity", c_int),
        ("win_gravity", c_int),
        ("backing_store", c_int),
        ("backing_planes", c_ulong),
        ("backing_pixel", c_ulong),
        ("save_under", c_int),
        ("event_mask", c_long),
        ("do_not_propagate_mask", c_long),
        ("override_redirect", c_int),
        ("colormap", c_ulong),
        ("cursor", c_ulong),
    ]


class XWindowAttributes(Structure):
    _fields_ = [
        ("x", c_int),
        ("y", c_int),
        ("width", c_int),
        ("height", c_int),
        ("border_width", c_int),
        ("depth", c_int),
        ("visual", c_void_p),
        ("root", c_ulong),
        ("class_", c_int),
        ("bit_gravity", c_int),
        ("win_gravity", c_int),
        ("backing_store", c_int),
        ("backing_planes", c_ulong),
        ("backing_pixel", c_ulong),
        ("save_under", c_int),
        ("colormap", c_ulong),
        ("map_installed", c_int),
        ("map_state", c_int),
        ("all_event_masks", c_long),
        ("your_event_mask", c_long),
        ("do_not_propagate_mask", c_long),
        ("override_redirect", c_int),
        ("screen", c_void_p),
    ]


class XColor(Structure):
    _fields_ = [
        ("pixel", c_ulong),
        ("red", c_ushort),
        ("green", c_ushort),
        ("blue", c_ushort),
        ("flags", ctypes.c_char),
        ("pad", ctypes.c_char),
    ]


class XExposeEvent(Structure):
    _fields_ = [
        ("type", c_int),
        ("serial", c_ulong),
        ("send_event", c_int),
        ("display", c_void_p),
        ("window", c_ulong),
        ("x", c_int),
        ("y", c_int),
        ("width", c_int),
        ("height", c_int),
        ("count", c_int),
    ]


class XEvent(Union):
    _fields_ = [("type", c_int), ("expose", XExposeEvent), ("pad", c_long * 24)]


class XCharStruct(Structure):
    _fields_ = [
        ("lbearing", c_short),
        ("rbearing", c_short),
        ("width", c_short),
        ("ascent", c_short),
        ("descent", c_short),
        ("attributes", c_ushort),
    ]


class XFontStruct(Structure):
    _fields_ = [
        ("ext_data", c_void_p),
        ("fid", c_ulong),
        ("direction", c_uint),
        ("min_char_or_byte2", c_uint),
        ("max_char_or_byte2", c_uint),
        ("min_byte1", c_uint),
        ("max_byte1", c_uint),
        ("all_chars_exist", c_int),
        ("default_char", c_uint),
        ("n_properties", c_int),
        ("properties", c_void_p),
        ("min_bounds", XCharStruct),
        ("max_bounds", XCharStruct),
        ("per_char", c_void_p),
        ("ascent", c_int),
        ("descent", c_int),
    ]


class XRenderColor(Structure):
    _fields_ = [
        ("red", c_ushort),
        ("green", c_ushort),
        ("blue", c_ushort),
        ("alpha", c_ushort),
    ]


class XftColor(Structure):
    _fields_ = [("pixel", c_ulong), ("color", XRenderColor)]


class XftFont(Structure):
    _fields_ = [
        ("ascent", c_int),
        ("descent", c_int),
        ("height", c_int),
        ("max_advance_width", c_int),
        ("charset", c_void_p),
        ("pattern", c_void_p),
    ]


class XGlyphInfo(Structure):
    _fields_ = [
        ("width", c_ushort),
        ("height", c_ushort),
        ("x", c_short),
        ("y", c_short),
        ("xOff", c_short),
        ("yOff", c_short),
    ]


class XRRMonitorInfo(Structure):
    _fields_ = [
        ("name", c_ulong),
        ("primary", c_int),
        ("automatic", c_int),
        ("noutput", c_int),
        ("x", c_int),
        ("y", c_int),
        ("width", c_int),
        ("height", c_int),
        ("mwidth", c_int),
        ("mheight", c_int),
        ("outputs", c_void_p),
    ]


ERROR_HANDLER = CFUNCTYPE(c_int, c_void_p, c_void_p)
D, W = c_void_p, c_ulong  # a Display*, an XID (a window, a pixmap, an atom, a colormap, a font)
GC = c_void_p
UINT = c_uint

X11 = {  # name: result, then arguments
    "XOpenDisplay": (D, c_char_p),
    "XCloseDisplay": (c_int, D),
    "XDefaultScreen": (c_int, D),
    "XDefaultRootWindow": (W, D),
    "XDisplayWidth": (c_int, D, c_int),
    "XDisplayHeight": (c_int, D, c_int),
    "XDefaultVisual": (c_void_p, D, c_int),
    "XDefaultColormap": (W, D, c_int),
    "XDefaultDepth": (c_int, D, c_int),
    "XWhitePixel": (c_ulong, D, c_int),
    "XConnectionNumber": (c_int, D),
    "XCreateWindow": (
        W,
        D,
        W,
        c_int,
        c_int,
        UINT,
        UINT,
        UINT,
        c_int,
        UINT,
        c_void_p,
        c_ulong,
        POINTER(XSetWindowAttributes),
    ),
    "XCreatePixmap": (W, D, W, UINT, UINT, UINT),
    "XFreePixmap": (c_int, D, W),
    "XCopyArea": (c_int, D, W, W, GC, c_int, c_int, UINT, UINT, c_int, c_int),
    "XMapRaised": (c_int, D, W),
    "XRaiseWindow": (c_int, D, W),
    "XDestroyWindow": (c_int, D, W),
    "XFlush": (c_int, D),
    "XSync": (c_int, D, c_int),
    "XPending": (c_int, D),
    "XNextEvent": (c_int, D, POINTER(XEvent)),
    "XCreateGC": (GC, D, W, c_ulong, c_void_p),
    "XFreeGC": (c_int, D, GC),
    "XSetForeground": (c_int, D, GC, c_ulong),
    "XSetFont": (c_int, D, GC, W),
    "XFillRectangle": (c_int, D, W, GC, c_int, c_int, UINT, UINT),
    "XFillArc": (c_int, D, W, GC, c_int, c_int, UINT, UINT, c_int, c_int),
    "XAllocColor": (c_int, D, W, POINTER(XColor)),
    "XLoadQueryFont": (POINTER(XFontStruct), D, c_char_p),
    "XDrawString16": (c_int, D, W, GC, c_int, c_int, c_char_p, c_int),
    "XTextWidth16": (c_int, POINTER(XFontStruct), c_char_p, c_int),
    "XInternAtom": (W, D, c_char_p, c_int),
    "XGetWindowProperty": (
        c_int,
        D,
        W,
        W,
        c_long,
        c_long,
        c_int,
        W,
        POINTER(c_ulong),
        POINTER(c_int),
        POINTER(c_ulong),
        POINTER(c_ulong),
        POINTER(POINTER(c_ulong)),
    ),
    "XFree": (c_int, c_void_p),
    "XGetWindowAttributes": (c_int, D, W, POINTER(XWindowAttributes)),
    "XTranslateCoordinates": (
        c_int,
        D,
        W,
        W,
        c_int,
        c_int,
        POINTER(c_int),
        POINTER(c_int),
        POINTER(c_ulong),
    ),
    "XSetErrorHandler": (c_void_p, ERROR_HANDLER),
    "XGetDefault": (c_char_p, D, c_char_p, c_char_p),
    "XChangeProperty": (c_int, D, W, W, W, c_int, c_int, POINTER(c_ulong), c_int),
}

XFT = {
    "XftFontOpenName": (POINTER(XftFont), D, c_int, c_char_p),
    "XftDrawCreate": (c_void_p, D, W, c_void_p, W),
    "XftDrawDestroy": (None, c_void_p),
    "XftDrawStringUtf8": (
        None,
        c_void_p,
        POINTER(XftColor),
        POINTER(XftFont),
        c_int,
        c_int,
        c_char_p,
        c_int,
    ),
    "XftTextExtentsUtf8": (None, D, POINTER(XftFont), c_char_p, c_int, POINTER(XGlyphInfo)),
    "XftColorAllocValue": (c_int, D, c_void_p, W, POINTER(XRenderColor), POINTER(XftColor)),
}

XRANDR = {
    "XRRGetMonitors": (POINTER(XRRMonitorInfo), D, W, c_int, POINTER(c_int)),
    "XRRFreeMonitors": (None, POINTER(XRRMonitorInfo)),
}


def bound(soname: str, table: dict):
    """The library, its functions told their types: pointers are 64 bits, ctypes' default 32."""
    lib = ctypes.CDLL(soname)
    for name, (result, *args) in table.items():
        function = getattr(lib, name)
        function.restype = result
        function.argtypes = args
    return lib


def optional(soname: str, table: dict):
    with contextlib.suppress(OSError, AttributeError):
        return bound(soname, table)
    return None


def chars16(text: str, narrow: bool) -> bytes:
    """``text`` as an X fixed font's 16-bit characters; what it lacks as ``?``."""
    out = bytearray()
    for character in text:
        code = ord(character)
        if code > 0xFFFF or (narrow and code > 0xFF):
            code = ord("?")
        out += bytes((code >> 8, code & 0xFF))
    return bytes(out)


class Panel:
    """The window and what it needs of X. :class:`OSError` without libX11 or a display."""

    def __init__(self, xplane_pid: int) -> None:
        self.pid = xplane_pid
        self.x = bound("libX11.so.6", X11)
        self.xft = optional("libXft.so.2", XFT)
        self.xrandr = optional("libXrandr.so.2", XRANDR)
        self.dpy = self.x.XOpenDisplay(None)
        if not self.dpy:
            raise OSError("no display")
        # Xlib's default handler ends the process: X-Plane's windows may go at any time
        self._quiet = ERROR_HANDLER(lambda _display, _error: 0)
        self.x.XSetErrorHandler(self._quiet)
        self.screen = self.x.XDefaultScreen(self.dpy)
        self.root = self.x.XDefaultRootWindow(self.dpy)
        self.visual = self.x.XDefaultVisual(self.dpy, self.screen)
        self.colormap = self.x.XDefaultColormap(self.dpy, self.screen)
        self.scale = 1.0
        dpi = self.x.XGetDefault(self.dpy, b"Xft", b"dpi")  # the desktop's scale, as Xft takes it
        with contextlib.suppress(ValueError, TypeError):
            if float(dpi) > 96.0:
                self.scale = float(dpi) / 96.0
        self.gc = self.x.XCreateGC(self.dpy, self.root, 0, None)
        self.pixel: list[int] = []
        self.ftcolour: list[XftColor] = []
        self._colours()
        self.fonts = [self._font(i) for i in range(len(FONT_PX))]
        self.win = 0
        self.picture = 0
        self.width = self.px(PANEL_W)
        self.height = self.px(PANEL_H)
        self.shown = ("", "", 0.0)
        self.quieted: list[tuple[int, list[int]]] = []

    def px(self, points: float) -> int:
        return int(points * self.scale + 0.5)

    def _colours(self) -> None:
        for r, g, b in RGB:
            c = XColor(0, r * 257, g * 257, b * 257, b"\x07", b"\0")  # DoRed | DoGreen | DoBlue
            ok = self.x.XAllocColor(self.dpy, self.colormap, byref(c))
            self.pixel.append(c.pixel if ok else self.x.XWhitePixel(self.dpy, self.screen))
            colour = XftColor()
            if self.xft is not None and not self.xft.XftColorAllocValue(
                self.dpy,
                self.visual,
                self.colormap,
                byref(XRenderColor(r * 257, g * 257, b * 257, 0xFFFF)),
                byref(colour),
            ):
                self.xft = None
            self.ftcolour.append(colour)

    def _font(self, index: int):
        """``(True, an Xft font)`` where Xft is there, else ``(False, an X fixed font)``."""
        if self.xft is not None:
            name = f"sans-serif:pixelsize={self.px(FONT_PX[index])}".encode()
            font = self.xft.XftFontOpenName(self.dpy, self.screen, name)
            if font:
                return True, font
        font = self.x.XLoadQueryFont(self.dpy, FIXED_FONTS[index])
        return False, font or self.x.XLoadQueryFont(self.dpy, b"fixed")

    def _atom(self, name: bytes) -> int:
        return self.x.XInternAtom(self.dpy, name, 1)  # 0 where nobody has made it

    def _property(self, window: int, atom: int, kind: int, count: int) -> list[int] | None:
        """A property of 32-bit items, whole; None where it is not there or does not fit."""
        got, form, n, left = c_ulong(), c_int(), c_ulong(), c_ulong()
        value = POINTER(c_ulong)()
        status = self.x.XGetWindowProperty(
            self.dpy,
            window,
            atom,
            0,
            count,
            0,
            kind,
            byref(got),
            byref(form),
            byref(n),
            byref(left),
            byref(value),
        )
        if status != 0 or not value:
            return None
        try:
            return (
                [value[i] for i in range(n.value)] if form.value == 32 and not left.value else None
            )
        finally:
            self.x.XFree(value)

    def xplane_windows(self) -> list[int]:
        """X-Plane's process's windows in the window manager's list."""
        clients, pid = self._atom(b"_NET_CLIENT_LIST"), self._atom(b"_NET_WM_PID")
        if not clients or not pid or not self.pid:
            return []
        return [
            w
            for w in self._property(self.root, clients, XA_WINDOW, 4096) or []
            if self._property(w, pid, XA_CARDINAL, 1) == [self.pid]
        ]

    def around_xplane(self) -> tuple[int, int, int, int]:
        """X-Plane's largest window on the screen, else the primary monitor, else all of it."""
        x, y = 0, 0
        w = self.x.XDisplayWidth(self.dpy, self.screen)
        h = self.x.XDisplayHeight(self.dpy, self.screen)
        if self.xrandr is not None:
            n = c_int()
            monitors = self.xrandr.XRRGetMonitors(self.dpy, self.root, 1, byref(n))
            if monitors:
                for i in range(n.value):
                    m = monitors[i]
                    if m.primary or i == 0:
                        x, y, w, h = m.x, m.y, m.width, m.height
                self.xrandr.XRRFreeMonitors(monitors)
        best = 0
        for window in self.xplane_windows():
            a = XWindowAttributes()
            at_x, at_y, child = c_int(), c_int(), c_ulong()
            if (
                self.x.XGetWindowAttributes(self.dpy, window, byref(a))
                and a.map_state == IS_VIEWABLE
                and a.width * a.height > best
                and self.x.XTranslateCoordinates(
                    self.dpy, window, self.root, 0, 0, byref(at_x), byref(at_y), byref(child)
                )
            ):
                best = a.width * a.height
                x, y, w, h = at_x.value, at_y.value, a.width, a.height
        return x, y, w, h

    def quiet_pings(self) -> None:
        """X-Plane's windows stop saying they answer the window manager's pings."""
        protocols, ping = self._atom(b"WM_PROTOCOLS"), self._atom(b"_NET_WM_PING")
        if not protocols or not ping:
            return
        for window in self.xplane_windows():
            said = self._property(window, protocols, XA_ATOM, 16)
            if said and ping in said:
                self.quieted.append((window, said))
                self._set_protocols(window, protocols, [a for a in said if a != ping])
        self.x.XSync(self.dpy, 0)

    def answer_pings(self) -> None:
        """They say it again, as before."""
        protocols = self._atom(b"WM_PROTOCOLS")
        for window, said in self.quieted:
            self._set_protocols(window, protocols, said)
        self.quieted = []
        self.x.XSync(self.dpy, 0)

    def _set_protocols(self, window: int, protocols: int, atoms: list[int]) -> None:
        values = (c_ulong * len(atoms))(*atoms)
        self.x.XChangeProperty(self.dpy, window, protocols, XA_ATOM, 32, 0, values, len(atoms))

    def _width_of(self, font: tuple, text: str) -> int:
        ft, handle = font
        if ft:
            glyph = XGlyphInfo()
            raw = text.encode()
            self.xft.XftTextExtentsUtf8(self.dpy, handle, raw, len(raw), byref(glyph))
            return glyph.xOff
        if handle:
            return self.x.XTextWidth16(
                handle, chars16(text, handle.contents.max_byte1 == 0), len(text)
            )
        return 0

    def _text(self, draw, text: str, index: int, colour: int, top: int, box_h: int) -> None:
        """``text`` centred in the panel's width less 20 points a side, in the box's height, cut
        with an ellipsis where it does not fit."""
        font = self.fonts[index]
        ft, handle = font
        if not handle:
            return
        room = self.px(PANEL_W - 40)
        dots = "…" if ft else "..."
        shown, cut = text, len(text)
        width = self._width_of(font, shown)
        while width > room and cut > 0:
            cut -= 1
            shown = text[:cut] + dots
            width = self._width_of(font, shown)
        x = self.px(20) + (room - width) // 2
        base = (
            self.px(top) + (self.px(box_h) + handle.contents.ascent - handle.contents.descent) // 2
        )
        if ft:
            raw = shown.encode()
            self.xft.XftDrawStringUtf8(
                draw, byref(self.ftcolour[colour]), handle, x, base, raw, len(raw)
            )
        else:
            self.x.XSetForeground(self.dpy, self.gc, self.pixel[colour])
            self.x.XSetFont(self.dpy, self.gc, handle.contents.fid)
            wide = chars16(shown, handle.contents.max_byte1 == 0)
            self.x.XDrawString16(self.dpy, self.picture, self.gc, x, base, wide, len(shown))

    def _bar(self, points: float, colour: int) -> None:
        r, width = self.px(BAR_H), self.px(points)
        left, top = self.px((PANEL_W - BAR_W) / 2), self.px(PANEL_H - 16 - BAR_H)
        if width <= 0:
            return
        x = self.x
        x.XSetForeground(self.dpy, self.gc, self.pixel[colour])
        if width <= r:
            x.XFillArc(self.dpy, self.picture, self.gc, left, top, width, r, 0, 360 * 64)
            return
        x.XFillArc(self.dpy, self.picture, self.gc, left, top, r, r, 90 * 64, 180 * 64)
        x.XFillArc(self.dpy, self.picture, self.gc, left + width - r, top, r, r, 270 * 64, 180 * 64)
        x.XFillRectangle(self.dpy, self.picture, self.gc, left + r // 2, top, width - r, r)

    def _put(self) -> None:
        self.x.XCopyArea(
            self.dpy, self.picture, self.win, self.gc, 0, 0, self.width, self.height, 0, 0
        )

    def _draw(self) -> None:
        """Drawn apart, then put up in one go: never seen half drawn."""
        headline, detail, fraction = self.shown
        self.x.XSetForeground(self.dpy, self.gc, self.pixel[BG])
        self.x.XFillRectangle(self.dpy, self.picture, self.gc, 0, 0, self.width, self.height)
        draw = None
        if self.xft is not None:
            draw = self.xft.XftDrawCreate(self.dpy, self.picture, self.visual, self.colormap)
        self._text(draw, "OrthoStudio XP Live", TITLE_F, TITLE, 12, 20)
        self._text(draw, headline, HEAD_F, WHITE, 36, 26)
        self._text(draw, detail, DETAIL_F, DETAIL, 64, 20)
        if draw:
            self.xft.XftDrawDestroy(draw)
        self._bar(BAR_W, TRACK)
        self._bar(BAR_W * fraction, FILL)
        self._put()

    def _make(self) -> None:
        x, y, w, h = self.around_xplane()
        a = XSetWindowAttributes()
        a.override_redirect = 1  # above X-Plane, out of the window manager's hands, no keyboard
        a.background_pixel = a.border_pixel = self.pixel[BG]
        a.event_mask = EXPOSURE_MASK
        self.win = self.x.XCreateWindow(
            self.dpy,
            self.root,
            x + w // 2 - self.width // 2,
            y + h // 2 - self.height // 2,
            self.width,
            self.height,
            0,
            0,  # the depth of its parent
            INPUT_OUTPUT,
            None,  # the visual of its parent
            CW_OVERRIDE_REDIRECT | CW_BACK_PIXEL | CW_BORDER_PIXEL | CW_EVENT_MASK,
            byref(a),
        )
        depth = self.x.XDefaultDepth(self.dpy, self.screen)
        self.picture = self.x.XCreatePixmap(self.dpy, self.root, self.width, self.height, depth)
        self.x.XMapRaised(self.dpy, self.win)
        self.quiet_pings()

    def show(self, headline: str, detail: str, fraction: float) -> None:
        self.shown = (headline, detail, max(0.0, min(1.0, fraction)))
        if self.win:
            self.x.XRaiseWindow(self.dpy, self.win)
        else:
            self._make()
        self._draw()
        self.x.XFlush(self.dpy)

    def events(self) -> None:
        event = XEvent()
        while self.x.XPending(self.dpy) > 0:
            self.x.XNextEvent(self.dpy, byref(event))
            if event.type == EXPOSE and event.expose.count == 0 and self.win:
                self._put()

    def raised(self) -> None:
        if self.win:
            self.x.XRaiseWindow(self.dpy, self.win)
        self.x.XFlush(self.dpy)

    def close(self) -> None:
        self.answer_pings()
        if self.win:
            self.x.XDestroyWindow(self.dpy, self.win)
            self.x.XFreePixmap(self.dpy, self.picture)
        self.x.XFreeGC(self.dpy, self.gc)
        self.x.XSync(self.dpy, 0)
        self.x.XCloseDisplay(self.dpy)


def run(panel: Panel, stdin: int) -> None:
    """Until the end of ``stdin``: its last complete line shown, the window kept above X-Plane."""
    rest = b""
    connection = panel.x.XConnectionNumber(panel.dpy)
    while True:
        panel.events()
        ready, _, _ = select.select([stdin, connection], [], [], RAISE_S)
        if stdin in ready:
            data = os.read(stdin, 65536)
            if not data:
                return
            *said, rest = (rest + data).split(b"\n")
            if said:
                with contextlib.suppress(ValueError, TypeError):
                    headline, detail, fraction = json.loads(said[-1])
                    panel.show(str(headline), str(detail), float(fraction))
        elif not ready:
            panel.raised()


def main(argv: list[str]) -> int:
    pid = int(argv[1]) if len(argv) > 1 else 0
    try:
        panel = Panel(pid)
    except OSError:
        return 0  # no libX11, or no display: no window
    try:
        run(panel, sys.stdin.fileno())
    finally:
        panel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
