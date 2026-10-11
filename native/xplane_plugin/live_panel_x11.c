/* OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
 * Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE. */

/*
 * The plugin's loading window and its notice on Linux: what live_panel.m shows on the Mac, with the
 * same sizes, colours and words (live_gate.c says when), drawn with X11, on which X-Plane runs
 * (XWayland under Wayland).
 *
 * libX11 is opened when a window is first wanted (dlopen), never linked: a computer without it
 * still loads the plugin, and X-Plane waits for the ortho all the same, without the window. The
 * windows have a connection to the display of their own, used only by X-Plane's main thread, which
 * calls every function here: X-Plane's own connection is never touched. A window is drawn at once,
 * and the events (to draw again, the cross's click) are read each time X-Plane's thread comes here:
 * every ASK_EVERY_S while X-Plane waits (live_panel_tick), every second in flight
 * (live_note_closed), which is when the Mac's notice goes after its cross too.
 *
 * The windows are override-redirect: above X-Plane, out of the window manager's hands, never taking
 * X-Plane's keyboard; raised again each time. The text is drawn with Xft where it is installed
 * (sharp, every language), else with the X server's own fixed font. Where X-Plane's window is
 * comes from the window manager's list (_NET_CLIENT_LIST, _NET_WM_PID), else the primary monitor
 * (XRandR), else the whole screen. Xlib's default error handler ends the process: while windows
 * that are not Live's are looked at, a handler of Live's own takes the errors.
 *
 * While X-Plane waits, its own connection to the display is not read: GNOME's window manager,
 * whose pings X-Plane then leaves unanswered, would offer after 5 s to force it to quit. So for
 * the loading window's time X-Plane's windows stop saying they answer pings (_NET_WM_PING out of
 * their WM_PROTOCOLS), and say it again after (quiet_pings).
 *
 * Built with -DLIVE_NO_X11 where the X11 headers are missing (a test machine): no window then.
 */

#ifdef LIVE_NO_X11

void live_panel_show(const char *what, const char *progress, double fraction) {
    (void)what;
    (void)progress;
    (void)fraction;
}
void live_panel_hide(void) {}
static int pressed;
void live_note_press(void) { pressed = 1; }
int live_note_closed(void) {
    int c = pressed;
    pressed = 0;
    return c;
}
void live_note_show(const char *text, int french) {
    (void)text;
    (void)french;
}
void live_note_hide(void) {}
void live_panel_tick(void) {}
void live_panels_close(void) {}

#else

#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PANEL_W 560
#define PANEL_H 120
#define BAR_W 480
#define BAR_H 8
#define NOTE_H 44

/* Xft's and XRandR's few types, as their headers have them (unchanged for twenty years): their
   headers are not needed to build. */
typedef struct {
    unsigned short red, green, blue, alpha;
} RenderColor;
typedef struct {
    unsigned long pixel;
    RenderColor color;
} FtColor;
typedef struct {
    unsigned short width, height;
    short x, y, xOff, yOff;
} GlyphInfo;
typedef struct {
    int ascent, descent, height, max_advance_width;
    void *charset, *pattern;
} FtFont;
typedef struct {
    Atom name;
    Bool primary, automatic;
    int noutput, x, y, width, height, mwidth, mheight;
    void *outputs;
} Monitor;

static struct {
    Display *(*OpenDisplay)(const char *);
    int (*CloseDisplay)(Display *);
    Window (*CreateWindow)(Display *, Window, int, int, unsigned, unsigned, unsigned, int, unsigned, Visual *,
                           unsigned long, XSetWindowAttributes *);
    int (*MapRaised)(Display *, Window);
    int (*RaiseWindow)(Display *, Window);
    int (*DestroyWindow)(Display *, Window);
    int (*Flush)(Display *);
    int (*Sync)(Display *, Bool);
    int (*Pending)(Display *);
    int (*NextEvent)(Display *, XEvent *);
    GC (*CreateGC)(Display *, Drawable, unsigned long, XGCValues *);
    int (*FreeGC)(Display *, GC);
    int (*SetForeground)(Display *, GC, unsigned long);
    int (*SetFont)(Display *, GC, Font);
    int (*FillRectangle)(Display *, Drawable, GC, int, int, unsigned, unsigned);
    int (*FillArc)(Display *, Drawable, GC, int, int, unsigned, unsigned, int, int);
    Status (*AllocColor)(Display *, Colormap, XColor *);
    XFontStruct *(*LoadQueryFont)(Display *, const char *);
    int (*FreeFont)(Display *, XFontStruct *);
    int (*DrawString16)(Display *, Drawable, GC, int, int, const XChar2b *, int);
    int (*TextWidth16)(XFontStruct *, const XChar2b *, int);
    Atom (*InternAtom)(Display *, const char *, Bool);
    int (*GetWindowProperty)(Display *, Window, Atom, long, long, Bool, Atom, Atom *, int *, unsigned long *,
                             unsigned long *, unsigned char **);
    int (*Free)(void *);
    Status (*GetWindowAttributes)(Display *, Window, XWindowAttributes *);
    Bool (*TranslateCoordinates)(Display *, Window, Window, int, int, int *, int *, Window *);
    XErrorHandler (*SetErrorHandler)(XErrorHandler);
    char *(*GetDefault)(Display *, const char *, const char *);
    int (*ChangeProperty)(Display *, Window, Atom, Atom, int, int, const unsigned char *, int);
} X;

static struct {
    FtFont *(*FontOpenName)(Display *, int, const char *);
    void (*FontClose)(Display *, FtFont *);
    void *(*DrawCreate)(Display *, Drawable, Visual *, Colormap);
    void (*DrawDestroy)(void *);
    void (*DrawStringUtf8)(void *, const FtColor *, FtFont *, int, int, const unsigned char *, int);
    void (*TextExtentsUtf8)(Display *, FtFont *, const unsigned char *, int, GlyphInfo *);
    Bool (*ColorAllocValue)(Display *, Visual *, Colormap, const RenderColor *, FtColor *);
} Xft;

static struct {
    Monitor *(*GetMonitors)(Display *, Window, Bool, int *);
    void (*FreeMonitors)(Monitor *);
} Xrandr;

static int tried, xft;                   /* X11 looked for once; Xft found */
static Display *dpy;
static Window root, panel, note;
static GC gc;
static double scale = 1.0;
static int pressed;                      /* the notice's cross clicked */
static int note_french;
static char what_now[200], progress_now[100], note_now[160];
static double fraction_now;
static int cross_x, cross_y, cross_w;    /* the cross, in the notice's window */
static int note_w;

/* the colours: background, title, white, detail, track, fill, cross */
enum { BG, TITLE, WHITE, DETAIL, TRACK, FILL, CROSS, COLOURS };
static const unsigned char rgb[COLOURS][3] = {{20, 20, 20},  {153, 153, 153}, {255, 255, 255}, {204, 204, 204},
                                              {77, 77, 77}, {64, 153, 255},  {191, 191, 191}};
static unsigned long pixel[COLOURS];
static FtColor ftcolour[COLOURS];
enum { TITLE_F, HEAD_F, DETAIL_F, NOTE_F, CROSS_F, FONTS };
static const int font_px[FONTS] = {12, 17, 13, 13, 17};
static FtFont *ftfont[FONTS];
static XFontStruct *fixed[FONTS];

static void *find(void *lib, const char *name) { return lib ? dlsym(lib, name) : NULL; }

#define LOAD(table, lib, field, prefix)                                                                      \
    do {                                                                                                     \
        *(void **)&table.field = find(lib, prefix #field);                                                   \
        if (!table.field) ok = 0;                                                                            \
    } while (0)

static int quiet(Display *d, XErrorEvent *e) {
    (void)d;
    (void)e;
    return 0;
}

static int px(double points) { return (int)(points * scale + 0.5); }

static void open_fonts(void) {
    int i;
    char name[64];
    static const char *fixed_names[FONTS] = {
        "-misc-fixed-medium-r-normal--13-*-*-*-*-*-iso10646-1", "-misc-fixed-medium-r-normal--18-*-*-*-*-*-iso10646-1",
        "-misc-fixed-medium-r-normal--13-*-*-*-*-*-iso10646-1", "-misc-fixed-medium-r-normal--13-*-*-*-*-*-iso10646-1",
        "-misc-fixed-medium-r-normal--18-*-*-*-*-*-iso10646-1"};
    for (i = 0; i < FONTS; i++) {
        if (xft) {
            snprintf(name, sizeof name, "sans-serif:pixelsize=%d", px(font_px[i]));
            ftfont[i] = Xft.FontOpenName(dpy, DefaultScreen(dpy), name);
        }
        if (!ftfont[i]) {
            fixed[i] = X.LoadQueryFont(dpy, fixed_names[i]);
            if (!fixed[i]) fixed[i] = X.LoadQueryFont(dpy, "fixed");
        }
    }
}

/* libX11 (and Xft, XRandR if there), the display, the colours and fonts: once. 0 without. */
static int ready(void) {
    void *lib, *ft, *randr;
    int ok = 1, i;
    const char *dpi;
    if (tried) return dpy != NULL;
    tried = 1;
    lib = dlopen("libX11.so.6", RTLD_NOW | RTLD_LOCAL);
    if (!lib) return 0;
    LOAD(X, lib, OpenDisplay, "X");
    LOAD(X, lib, CloseDisplay, "X");
    LOAD(X, lib, CreateWindow, "X");
    LOAD(X, lib, MapRaised, "X");
    LOAD(X, lib, RaiseWindow, "X");
    LOAD(X, lib, DestroyWindow, "X");
    LOAD(X, lib, Flush, "X");
    LOAD(X, lib, Sync, "X");
    LOAD(X, lib, Pending, "X");
    LOAD(X, lib, NextEvent, "X");
    LOAD(X, lib, CreateGC, "X");
    LOAD(X, lib, FreeGC, "X");
    LOAD(X, lib, SetForeground, "X");
    LOAD(X, lib, SetFont, "X");
    LOAD(X, lib, FillRectangle, "X");
    LOAD(X, lib, FillArc, "X");
    LOAD(X, lib, AllocColor, "X");
    LOAD(X, lib, LoadQueryFont, "X");
    LOAD(X, lib, FreeFont, "X");
    LOAD(X, lib, DrawString16, "X");
    LOAD(X, lib, TextWidth16, "X");
    LOAD(X, lib, InternAtom, "X");
    LOAD(X, lib, GetWindowProperty, "X");
    LOAD(X, lib, Free, "X");
    LOAD(X, lib, GetWindowAttributes, "X");
    LOAD(X, lib, TranslateCoordinates, "X");
    LOAD(X, lib, SetErrorHandler, "X");
    LOAD(X, lib, GetDefault, "X");
    LOAD(X, lib, ChangeProperty, "X");
    if (!ok || !(dpy = X.OpenDisplay(NULL))) return 0;
    root = DefaultRootWindow(dpy);

    ft = dlopen("libXft.so.2", RTLD_NOW | RTLD_LOCAL);
    if (ft) {
        ok = 1;
        LOAD(Xft, ft, FontOpenName, "Xft");
        LOAD(Xft, ft, FontClose, "Xft");
        LOAD(Xft, ft, DrawCreate, "Xft");
        LOAD(Xft, ft, DrawDestroy, "Xft");
        LOAD(Xft, ft, DrawStringUtf8, "Xft");
        LOAD(Xft, ft, TextExtentsUtf8, "Xft");
        LOAD(Xft, ft, ColorAllocValue, "Xft");
        xft = ok;
    }
    randr = dlopen("libXrandr.so.2", RTLD_NOW | RTLD_LOCAL);
    if (randr) {
        ok = 1;
        LOAD(Xrandr, randr, GetMonitors, "XRR");
        LOAD(Xrandr, randr, FreeMonitors, "XRR");
        if (!ok) Xrandr.GetMonitors = NULL;
    }

    dpi = X.GetDefault(dpy, "Xft", "dpi"); /* the desktop's scale, as Xft programs take it */
    if (dpi && atof(dpi) > 96.0) scale = atof(dpi) / 96.0;
    for (i = 0; i < COLOURS; i++) {
        XColor c;
        RenderColor r;
        c.red = (unsigned short)(rgb[i][0] * 257);
        c.green = (unsigned short)(rgb[i][1] * 257);
        c.blue = (unsigned short)(rgb[i][2] * 257);
        c.flags = DoRed | DoGreen | DoBlue;
        pixel[i] = X.AllocColor(dpy, DefaultColormap(dpy, DefaultScreen(dpy)), &c) ? c.pixel
                                                                                    : WhitePixel(dpy, DefaultScreen(dpy));
        r.red = c.red;
        r.green = c.green;
        r.blue = c.blue;
        r.alpha = 0xffff;
        if (xft && !Xft.ColorAllocValue(dpy, DefaultVisual(dpy, DefaultScreen(dpy)),
                                        DefaultColormap(dpy, DefaultScreen(dpy)), &r, &ftcolour[i]))
            xft = 0;
    }
    gc = X.CreateGC(dpy, root, 0, NULL);
    open_fonts();
    return 1;
}

/* This process's windows in the window manager's list: X-Plane's. The caller has the quiet error
   handler in place (they are not Live's). */
#define MAX_WINDOWS 16
static int xplane_windows(Window *out) {
    Atom list = X.InternAtom(dpy, "_NET_CLIENT_LIST", True), pid = X.InternAtom(dpy, "_NET_WM_PID", True);
    Atom type;
    int format, found = 0;
    unsigned long count = 0, after, i;
    unsigned char *windows = NULL;
    if (list == None || pid == None) return 0;
    if (X.GetWindowProperty(dpy, root, list, 0, 4096, False, XA_WINDOW, &type, &format, &count, &after, &windows) ==
            Success &&
        windows && format == 32) {
        for (i = 0; i < count && found < MAX_WINDOWS; i++) {
            Window win = ((Window *)windows)[i];
            unsigned char *value = NULL;
            unsigned long n = 0, left;
            if (X.GetWindowProperty(dpy, win, pid, 0, 1, False, XA_CARDINAL, &type, &format, &n, &left, &value) ==
                    Success &&
                value && n == 1 && format == 32 && (pid_t)((unsigned long *)value)[0] == getpid())
                out[found++] = win;
            if (value) X.Free(value);
        }
    }
    if (windows) X.Free(windows);
    return found;
}

/* X-Plane's window on the screen (this process's largest), else the primary monitor, else all. */
static void around_xplane(int *x, int *y, int *w, int *h) {
    XErrorHandler before = X.SetErrorHandler(quiet);
    Window windows[MAX_WINDOWS];
    long best = 0;
    int i, found;
    *x = 0;
    *y = 0;
    *w = DisplayWidth(dpy, DefaultScreen(dpy));
    *h = DisplayHeight(dpy, DefaultScreen(dpy));
    if (Xrandr.GetMonitors) {
        int n = 0;
        Monitor *m = Xrandr.GetMonitors(dpy, root, True, &n);
        for (i = 0; m && i < n; i++) {
            if (m[i].primary || i == 0) {
                *x = m[i].x;
                *y = m[i].y;
                *w = m[i].width;
                *h = m[i].height;
            }
        }
        if (m) Xrandr.FreeMonitors(m);
    }
    found = xplane_windows(windows);
    for (i = 0; i < found; i++) {
        XWindowAttributes a;
        Window child;
        int at_x, at_y;
        if (X.GetWindowAttributes(dpy, windows[i], &a) && a.map_state == IsViewable && (long)a.width * a.height > best &&
            X.TranslateCoordinates(dpy, windows[i], root, 0, 0, &at_x, &at_y, &child)) {
            best = (long)a.width * a.height;
            *x = at_x;
            *y = at_y;
            *w = a.width;
            *h = a.height;
        }
    }
    X.Sync(dpy, False); /* every error of the look in, while the quiet handler takes them */
    X.SetErrorHandler(before);
}

/* X-Plane's windows whose pings are quieted, and what their WM_PROTOCOLS said before. */
#define MAX_PROTOCOLS 16
static struct {
    Window win;
    Atom protocols[MAX_PROTOCOLS];
    int n;
} quieted[MAX_WINDOWS];
static int n_quieted;

/* 1: X-Plane's windows stop saying they answer the window manager's pings (_NET_WM_PING), for as
   long as it waits; 0: they say it again, as before. */
static void quiet_pings(int quiet_now) {
    Atom protocols = X.InternAtom(dpy, "WM_PROTOCOLS", True), ping = X.InternAtom(dpy, "_NET_WM_PING", True);
    XErrorHandler before;
    int i;
    if (protocols == None || ping == None || (quiet_now && n_quieted) || (!quiet_now && !n_quieted)) return;
    before = X.SetErrorHandler(quiet);
    if (quiet_now) {
        Window windows[MAX_WINDOWS];
        int found = xplane_windows(windows);
        for (i = 0; i < found; i++) {
            Atom type, kept[MAX_PROTOCOLS];
            int format, n_kept = 0, had_ping = 0;
            unsigned long n = 0, left, j;
            unsigned char *value = NULL;
            if (X.GetWindowProperty(dpy, windows[i], protocols, 0, MAX_PROTOCOLS, False, XA_ATOM, &type, &format, &n,
                                    &left, &value) == Success &&
                value && format == 32 && left == 0) { /* all of them read: given back whole */
                for (j = 0; j < n; j++) {
                    Atom a = (Atom)((unsigned long *)value)[j];
                    quieted[n_quieted].protocols[j] = a;
                    if (a == ping)
                        had_ping = 1;
                    else
                        kept[n_kept++] = a;
                }
                if (had_ping) {
                    quieted[n_quieted].win = windows[i];
                    quieted[n_quieted].n = (int)n;
                    n_quieted++;
                    X.ChangeProperty(dpy, windows[i], protocols, XA_ATOM, 32, PropModeReplace,
                                     (const unsigned char *)kept, n_kept);
                }
            }
            if (value) X.Free(value);
        }
    } else {
        for (i = 0; i < n_quieted; i++)
            X.ChangeProperty(dpy, quieted[i].win, protocols, XA_ATOM, 32, PropModeReplace,
                             (const unsigned char *)quieted[i].protocols, quieted[i].n);
        n_quieted = 0;
    }
    X.Sync(dpy, False);
    X.SetErrorHandler(before);
}

/* UTF-8 as the fixed font's 16-bit characters: what it lacks (or a font of 8 bits beyond 255) as '?'. */
static int chars16(const char *s, XChar2b *out, int size, const XFontStruct *f) {
    const unsigned char *p = (const unsigned char *)s;
    int n = 0;
    while (*p && n < size) {
        unsigned c = *p++;
        if (c >= 0xc0) {
            int more = c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : 1;
            c &= more == 3 ? 0x07 : more == 2 ? 0x0f : 0x1f;
            while (more-- && (*p & 0xc0) == 0x80) c = (c << 6) | (*p++ & 0x3f);
        } else if (c >= 0x80) {
            c = '?';
        }
        if (c > 0xffff || (f->max_byte1 == 0 && c > 0xff)) c = '?';
        out[n].byte1 = (unsigned char)(c >> 8);
        out[n].byte2 = (unsigned char)(c & 0xff);
        n++;
    }
    return n;
}

static int width_of(int f, const char *s, int len) {
    if (ftfont[f]) {
        GlyphInfo g;
        Xft.TextExtentsUtf8(dpy, ftfont[f], (const unsigned char *)s, len, &g);
        return g.xOff;
    }
    if (fixed[f]) {
        XChar2b c[256];
        char part[512];
        snprintf(part, sizeof part, "%.*s", len, s);
        return X.TextWidth16(fixed[f], c, chars16(part, c, 256, fixed[f]));
    }
    return 0;
}

/* `s` in box (x, y, w, h), its width cut with an ellipsis where it does not fit: centred, or from
   the left. The box in the Mac's pixels, scaled. */
static void text_in(Window win, void *draw, const char *s, int f, int colour, int x, int y, int w, int h,
                    int centred) {
    char shown[512];
    int len = (int)strlen(s), width, at, base;
    const char *dots = ftfont[f] ? "\xe2\x80\xa6" : "...";
    snprintf(shown, sizeof shown, "%s", s);
    width = width_of(f, shown, (int)strlen(shown));
    while (width > px(w) && len > 0) { /* one character less, and the ellipsis */
        do len--;
        while (len > 0 && ((unsigned char)s[len] & 0xc0) == 0x80);
        snprintf(shown, sizeof shown, "%.*s%s", len, s, dots);
        width = width_of(f, shown, (int)strlen(shown));
    }
    at = px(x) + (centred ? (px(w) - width) / 2 : 0);
    if (ftfont[f]) {
        base = px(y) + (px(h) + ftfont[f]->ascent - ftfont[f]->descent) / 2;
        Xft.DrawStringUtf8(draw, &ftcolour[colour], ftfont[f], at, base, (const unsigned char *)shown,
                           (int)strlen(shown));
    } else if (fixed[f]) {
        XChar2b c[256];
        base = px(y) + (px(h) + fixed[f]->ascent - fixed[f]->descent) / 2;
        X.SetFont(dpy, gc, fixed[f]->fid);
        X.SetForeground(dpy, gc, pixel[colour]);
        X.DrawString16(dpy, win, gc, at, base, c, chars16(shown, c, 256, fixed[f]));
    }
}

static void bar(Window win, int x, int y, int w, int colour) {
    int r = px(BAR_H), left = px(x), top = px(y), width = px(w);
    if (width <= 0) return;
    X.SetForeground(dpy, gc, pixel[colour]);
    if (width <= r) {
        X.FillArc(dpy, win, gc, left, top, (unsigned)width, (unsigned)r, 0, 360 * 64);
        return;
    }
    X.FillArc(dpy, win, gc, left, top, (unsigned)r, (unsigned)r, 90 * 64, 180 * 64);
    X.FillArc(dpy, win, gc, left + width - r, top, (unsigned)r, (unsigned)r, 270 * 64, 180 * 64);
    X.FillRectangle(dpy, win, gc, left + r / 2, top, (unsigned)(width - r), (unsigned)r);
}

/* One of Live's windows, never another: a request on a window gone would end X-Plane (Xlib's
   default error handler). */
static void draw(Window win) {
    void *ftdraw = NULL;
    if (!win || (win != panel && win != note)) return;
    X.SetForeground(dpy, gc, pixel[BG]);
    if (win == panel)
        X.FillRectangle(dpy, win, gc, 0, 0, (unsigned)px(PANEL_W), (unsigned)px(PANEL_H));
    else
        X.FillRectangle(dpy, win, gc, 0, 0, (unsigned)note_w, (unsigned)px(NOTE_H));
    if (xft) ftdraw = Xft.DrawCreate(dpy, win, DefaultVisual(dpy, DefaultScreen(dpy)),
                                     DefaultColormap(dpy, DefaultScreen(dpy)));
    if (win == panel) {
        text_in(win, ftdraw, "OrthoStudio XP Live", TITLE_F, TITLE, 20, 12, 520, 20, 1);
        text_in(win, ftdraw, what_now, HEAD_F, WHITE, 20, 36, 520, 26, 1);
        text_in(win, ftdraw, progress_now, DETAIL_F, DETAIL, 20, 64, 520, 20, 1);
        bar(win, (PANEL_W - BAR_W) / 2, PANEL_H - 16 - BAR_H, BAR_W, TRACK);
        bar(win, (PANEL_W - BAR_W) / 2, PANEL_H - 16 - BAR_H, (int)(BAR_W * fraction_now), FILL);
    } else {
        int text_w = (int)(note_w / scale) - 14 - 34;
        text_in(win, ftdraw, note_now, NOTE_F, WHITE, 14, 12, text_w, 20, 0);
        text_in(win, ftdraw, "\xc3\x97", CROSS_F, CROSS, (int)(cross_x / scale), (int)(cross_y / scale), 26, 26, 1);
    }
    if (ftdraw) Xft.DrawDestroy(ftdraw);
}

/* The events of Live's windows: drawn again where uncovered, the notice's cross clicked. */
static void events(void) {
    XEvent e;
    while (X.Pending(dpy) > 0) {
        X.NextEvent(dpy, &e);
        if (e.type == Expose && e.xexpose.count == 0)
            draw(e.xexpose.window); /* only Live's windows of now: an event may come from one gone */
        else if (e.type == ButtonRelease && e.xbutton.window == note && e.xbutton.button == Button1 &&
                 e.xbutton.x >= cross_x && e.xbutton.x < cross_x + cross_w && e.xbutton.y >= cross_y &&
                 e.xbutton.y < cross_y + cross_w)
            pressed = 1;
    }
}

static Window make(int x, int y, int w, int h) {
    XSetWindowAttributes a;
    Window win;
    memset(&a, 0, sizeof a);
    a.override_redirect = True;
    a.background_pixel = pixel[BG];
    a.border_pixel = pixel[BG];
    a.event_mask = ExposureMask | ButtonPressMask | ButtonReleaseMask;
    win = X.CreateWindow(dpy, root, x, y, (unsigned)w, (unsigned)h, 0, CopyFromParent, InputOutput, CopyFromParent,
                         CWOverrideRedirect | CWBackPixel | CWBorderPixel | CWEventMask, &a);
    X.MapRaised(dpy, win);
    X.Sync(dpy, False);
    return win;
}

void live_panel_show(const char *what, const char *progress, double fraction) {
    if (!ready()) return;
    snprintf(what_now, sizeof what_now, "%s", what ? what : "");
    snprintf(progress_now, sizeof progress_now, "%s", progress ? progress : "");
    fraction_now = fraction < 0 ? 0 : fraction > 1 ? 1 : fraction;
    if (!panel) {
        int x, y, w, h;
        around_xplane(&x, &y, &w, &h);
        panel = make(x + w / 2 - px(PANEL_W) / 2, y + h / 2 - px(PANEL_H) / 2, px(PANEL_W), px(PANEL_H));
        quiet_pings(1);
    } else {
        X.RaiseWindow(dpy, panel);
    }
    events();
    draw(panel);
    X.Flush(dpy);
}

void live_panel_hide(void) {
    if (!dpy || !panel) return;
    X.DestroyWindow(dpy, panel);
    panel = 0;
    quiet_pings(0);
    X.Flush(dpy);
}

void live_note_press(void) { pressed = 1; } /* its cross; the tests call it too */

int live_note_closed(void) {
    int c;
    if (dpy) {
        events();
        if (note) {
            X.RaiseWindow(dpy, note);
            X.Flush(dpy);
        }
    }
    c = pressed;
    pressed = 0;
    return c;
}

void live_note_hide(void) {
    if (!dpy || !note) return;
    X.DestroyWindow(dpy, note);
    note = 0;
    X.Flush(dpy);
}

void live_note_show(const char *what, int french) {
    int x, y, w, h, text_w;
    if (!ready()) return;
    if (note && note_french == french && strcmp(note_now, what) == 0) return; /* shown already */
    live_note_hide();
    snprintf(note_now, sizeof note_now, "%s", what);
    note_french = french;
    /* small, in the top right corner of X-Plane's window, below its menu bar's band; as wide as its
       words */
    text_w = width_of(NOTE_F, note_now, (int)strlen(note_now)) + px(8);
    if (text_w < px(270)) text_w = px(270);
    if (text_w > px(560)) text_w = px(560);
    note_w = px(14) + text_w + px(34);
    cross_w = px(26);
    cross_x = note_w - px(32);
    cross_y = px(9);
    around_xplane(&x, &y, &w, &h);
    note = make(x + w - note_w - px(16), y + px(48), note_w, px(NOTE_H));
    events();
    draw(note);
    X.Flush(dpy);
}

/* While X-Plane waits: Live's window drawn again if it was covered, and kept above. */
void live_panel_tick(void) {
    if (!dpy) return;
    events();
    if (panel) X.RaiseWindow(dpy, panel);
    X.Flush(dpy);
}

void live_panels_close(void) {
    int i;
    if (!dpy) return;
    live_panel_hide();
    live_note_hide();
    for (i = 0; i < FONTS; i++) {
        if (ftfont[i]) Xft.FontClose(dpy, ftfont[i]);
        if (fixed[i]) X.FreeFont(dpy, fixed[i]);
        ftfont[i] = NULL;
        fixed[i] = NULL;
    }
    X.FreeGC(dpy, gc);
    X.CloseDisplay(dpy);
    dpy = NULL;
}

#endif
