/* OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
 * Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE. */

/*
 * The plugin's loading window and its notice on Windows: what live_panel.m shows on the Mac, with
 * the same sizes, colours and words (live_gate.c says when).
 *
 * The windows belong to a thread of their own, which draws them and takes their clicks. X-Plane's
 * main thread, which calls every function here, only says what to show (`want`, under `lock`) and
 * wakes that thread: it is held while the loading window shows, and in flight nothing says that
 * X-Plane hands the messages of its thread to windows it does not know. The windows have no owner
 * (a window owned across threads joins the two threads' input, and X-Plane's is held). They stay
 * above X-Plane as the Mac's do (topmost), never take its keyboard (WS_EX_NOACTIVATE), and show
 * nothing in the taskbar.
 *
 * While X-Plane waits, live_panel_tick looks at its thread's messages without taking any
 * (PM_NOREMOVE): Windows takes a thread that has not looked for 5 s for a program that does not
 * respond, and greys its window ("Not Responding", IsHungAppWindow).
 *
 * The thread takes the DPI awareness of X-Plane's window: the text is as sharp as X-Plane's, and the
 * sizes, the Mac's in pixels at 96 DPI, are scaled to X-Plane's screen. It puts the windows back on
 * top every second (and the loading window at each update): a window X-Plane brings to the front,
 * topmost too, would cover them.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <string.h>
#include <wchar.h>

#define CLASS_NAME L"OrthoStudioXPLivePanel"
#define WM_LIVE_SYNC (WM_APP + 1)
#define ALPHA 235 /* the Mac's 0.92 */
#define BACKGROUND RGB(20, 20, 20)
#define PANEL_W 560
#define PANEL_H 140
#define BAR_W 480
#define BAR_H 8
#define NOTE_H 44
#define LOADING 1 /* a window's kind (GWLP_USERDATA): set before it is first drawn */
#define NOTICE 2

typedef UINT(WINAPI *dpi_for_window_f)(HWND);
typedef void *(WINAPI *window_awareness_f)(HWND);
typedef void *(WINAPI *set_thread_awareness_f)(void *);

static CRITICAL_SECTION lock;
static int lock_made;
static HANDLE thread;
static DWORD thread_id;
static volatile LONG pressed; /* the notice's cross, clicked since live_note_closed last looked */

/* What X-Plane's thread wants shown, under lock. */
static struct {
    int panel;
    wchar_t what[200], progress[120], building[120];
    double fraction;
    int note, french, note_serial; /* note_serial: a new notice, its window made again */
    wchar_t text[200];
    RECT around; /* X-Plane's window (its client area) on the screen */
    UINT dpi;
    void *awareness; /* X-Plane's window's DPI awareness context, or NULL */
} want;

/* The windows' thread's own. */
static HWND panel, note;
static int note_made; /* the note_serial its window shows */
static HINSTANCE module;
static ATOM klass;
static double scale = 1.0;
static HFONT title_font, head_font, detail_font, note_font, cross_font;
static RECT cross; /* the cross in the notice's window */

/* --- X-Plane's thread --------------------------------------------------------------------------- */

static FARPROC user32(const char *name) {
    HMODULE u = GetModuleHandleW(L"user32.dll");
    return u ? GetProcAddress(u, name) : NULL;
}

static void wide(const char *s, wchar_t *out, int size) {
    if (!MultiByteToWideChar(CP_UTF8, 0, s ? s : "", -1, out, size)) out[0] = 0;
}

struct biggest {
    HWND w;
    LONG area;
};

static BOOL CALLBACK bigger(HWND w, LPARAM p) {
    struct biggest *b = (struct biggest *)p;
    RECT r;
    if (IsWindowVisible(w) && !IsIconic(w) && GetWindowRect(w, &r)) { /* minimized: (-32000, -32000) */
        LONG area = (r.right - r.left) * (r.bottom - r.top);
        if (area > b->area) {
            b->w = w;
            b->area = area;
        }
    }
    return TRUE;
}

/* X-Plane's window, the largest of its thread's (the one calling): where, at what DPI. Under lock. */
static void look_at_xplane(void) {
    struct biggest b = {NULL, 0};
    dpi_for_window_f dpi_for_window = (dpi_for_window_f)(void *)user32("GetDpiForWindow");
    window_awareness_f awareness = (window_awareness_f)(void *)user32("GetWindowDpiAwarenessContext");
    EnumThreadWindows(GetCurrentThreadId(), bigger, (LPARAM)&b);
    want.dpi = 0;
    want.awareness = NULL;
    if (b.w) {
        POINT origin = {0, 0};
        RECT client;
        GetClientRect(b.w, &client);
        ClientToScreen(b.w, &origin);
        want.around.left = origin.x;
        want.around.top = origin.y;
        want.around.right = origin.x + client.right;
        want.around.bottom = origin.y + client.bottom;
        if (dpi_for_window) want.dpi = dpi_for_window(b.w);
        if (awareness) want.awareness = awareness(b.w);
    } else {
        SystemParametersInfoW(SPI_GETWORKAREA, 0, &want.around, 0);
    }
    if (want.dpi == 0) {
        HDC screen = GetDC(NULL);
        want.dpi = screen ? (UINT)GetDeviceCaps(screen, LOGPIXELSX) : 96;
        if (screen) ReleaseDC(NULL, screen);
    }
}

static DWORD WINAPI windows_thread(LPVOID ready);

/* The windows' thread, started at the first window wanted. */
static int running(void) {
    HANDLE ready;
    if (thread) return 1;
    if (!lock_made) {
        InitializeCriticalSection(&lock);
        lock_made = 1;
    }
    ready = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!ready) return 0;
    thread = CreateThread(NULL, 0, windows_thread, ready, 0, &thread_id);
    if (thread) WaitForSingleObject(ready, INFINITE); /* its queue made (a few calls): it gets what is posted */
    CloseHandle(ready);
    return thread != NULL;
}

static void wake(void) {
    if (thread) PostThreadMessageW(thread_id, WM_LIVE_SYNC, 0, 0);
}

void live_panel_show(const char *what, const char *progress, const char *building, double fraction) {
    if (!running()) return;
    EnterCriticalSection(&lock);
    if (!want.panel) look_at_xplane();
    want.panel = 1;
    wide(what, want.what, (int)(sizeof want.what / sizeof *want.what));
    wide(progress, want.progress, (int)(sizeof want.progress / sizeof *want.progress));
    wide(building ? building : "", want.building, (int)(sizeof want.building / sizeof *want.building));
    want.fraction = fraction < 0 ? 0 : fraction > 1 ? 1 : fraction;
    LeaveCriticalSection(&lock);
    wake();
}

void live_panel_hide(void) {
    if (!thread) return;
    EnterCriticalSection(&lock);
    want.panel = 0;
    LeaveCriticalSection(&lock);
    wake();
}

void live_note_press(void) { InterlockedExchange(&pressed, 1); } /* its cross; the tests call it too */

int live_note_closed(void) { return InterlockedExchange(&pressed, 0) != 0; }

void live_note_show(const char *what, int french) {
    wchar_t text[200];
    if (!running()) return;
    wide(what, text, (int)(sizeof text / sizeof *text));
    EnterCriticalSection(&lock);
    if (want.note && want.french == french && wcscmp(want.text, text) == 0) { /* shown already */
        LeaveCriticalSection(&lock);
        return;
    }
    look_at_xplane();
    want.note = 1;
    want.french = french;
    wcscpy(want.text, text);
    want.note_serial++;
    LeaveCriticalSection(&lock);
    wake();
}

void live_note_hide(void) {
    if (!thread) return;
    EnterCriticalSection(&lock);
    want.note = 0;
    LeaveCriticalSection(&lock);
    wake();
}

/* While X-Plane waits: its thread looks at its messages, takes none, and so is not "Not Responding". */
void live_panel_tick(void) {
    MSG m;
    PeekMessageW(&m, NULL, 0, 0, PM_NOREMOVE);
}

/* The plugin stops: the windows and their thread end. The thread holds this code loaded until it
   has (FreeLibraryAndExitThread): one late to end never runs in code Windows has unloaded, and
   keeps what it uses. */
void live_panels_close(void) {
    if (thread) {
        DWORD ended;
        PostThreadMessageW(thread_id, WM_QUIT, 0, 0);
        ended = WaitForSingleObject(thread, 5000);
        CloseHandle(thread);
        thread = NULL;
        thread_id = 0;
        if (ended != WAIT_OBJECT_0) return; /* still at work: its lock and its state left to it */
    }
    if (lock_made) {
        DeleteCriticalSection(&lock);
        lock_made = 0;
    }
    memset(&want, 0, sizeof want);
}

/* --- the windows' thread ----------------------------------------------------------------------- */

static int px(double points) { return (int)(points * scale + 0.5); }

static HFONT font(double size) {
    return CreateFontW(-px(size), 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                       CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_SWISS, L"Segoe UI");
}

static void forget_fonts(void) {
    HFONT *all[] = {&title_font, &head_font, &detail_font, &note_font, &cross_font};
    size_t i;
    for (i = 0; i < sizeof all / sizeof *all; i++) {
        if (*all[i]) DeleteObject(*all[i]);
        *all[i] = NULL;
    }
}

/* The fonts at the scale of the window about to be made. */
static void fonts_for(UINT dpi) {
    double s = (dpi ? dpi : 96) / 96.0;
    if (title_font && s == scale) return;
    forget_fonts();
    scale = s;
    title_font = font(12);
    head_font = font(17);
    detail_font = font(13);
    note_font = font(13);
    cross_font = font(17);
}

static void text_in(HDC dc, const wchar_t *s, HFONT f, COLORREF c, int x, int y, int w, int h, UINT align) {
    RECT r;
    r.left = px(x);
    r.top = px(y);
    r.right = px(x + w);
    r.bottom = px(y + h);
    SelectObject(dc, f);
    SetTextColor(dc, c);
    DrawTextW(dc, s, -1, &r, align | DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
}

static void bar(HDC dc, int x, int y, int w, COLORREF c) {
    HBRUSH b = CreateSolidBrush(c);
    HGDIOBJ old_brush = SelectObject(dc, b), old_pen = SelectObject(dc, GetStockObject(NULL_PEN));
    if (w > 0) RoundRect(dc, px(x), px(y), px(x + w) + 1, px(y + BAR_H) + 1, px(BAR_H), px(BAR_H));
    SelectObject(dc, old_brush);
    SelectObject(dc, old_pen);
    DeleteObject(b);
}

static void paint(HWND w, HDC to) {
    LONG_PTR kind = GetWindowLongPtrW(w, GWLP_USERDATA);
    RECT r;
    HDC dc;
    HBITMAP bitmap;
    HGDIOBJ before;
    HBRUSH background;
    GetClientRect(w, &r);
    dc = CreateCompatibleDC(to); /* drawn aside, then shown at once: no flicker */
    bitmap = CreateCompatibleBitmap(to, r.right, r.bottom);
    before = SelectObject(dc, bitmap);
    background = CreateSolidBrush(BACKGROUND);
    FillRect(dc, &r, background);
    DeleteObject(background);
    SetBkMode(dc, TRANSPARENT);
    EnterCriticalSection(&lock);
    if (kind == LOADING) {
        text_in(dc, L"OrthoStudio XP Live", title_font, RGB(153, 153, 153), 20, 12, 520, 20, DT_CENTER);
        text_in(dc, want.what, head_font, RGB(255, 255, 255), 20, 36, 520, 26, DT_CENTER);
        text_in(dc, want.progress, detail_font, RGB(204, 204, 204), 20, 64, 520, 20, DT_CENTER);
        text_in(dc, want.building, detail_font, RGB(204, 204, 204), 20, 84, 520, 20, DT_CENTER);
        bar(dc, (PANEL_W - BAR_W) / 2, PANEL_H - 16 - BAR_H, BAR_W, RGB(77, 77, 77));
        bar(dc, (PANEL_W - BAR_W) / 2, PANEL_H - 16 - BAR_H, (int)(BAR_W * want.fraction), RGB(64, 153, 255));
    } else if (kind == NOTICE) {
        int text_w = (int)((r.right / scale) - 14 - 34 + 0.5);
        text_in(dc, want.text, note_font, RGB(255, 255, 255), 14, 12, text_w, 20, DT_LEFT);
        SelectObject(dc, cross_font);
        SetTextColor(dc, RGB(191, 191, 191));
        DrawTextW(dc, L"×", -1, &cross, DT_CENTER | DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);
    }
    LeaveCriticalSection(&lock);
    BitBlt(to, 0, 0, r.right, r.bottom, dc, 0, 0, SRCCOPY);
    SelectObject(dc, before);
    DeleteObject(bitmap);
    DeleteDC(dc);
}

static LRESULT CALLBACK window_proc(HWND w, UINT m, WPARAM wp, LPARAM lp) {
    switch (m) {
    case WM_MOUSEACTIVATE:
        return MA_NOACTIVATE; /* a click never takes X-Plane's keyboard */
    case WM_ERASEBKGND:
        return 1;
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(w, &ps);
        paint(w, dc);
        EndPaint(w, &ps);
        return 0;
    }
    case WM_LBUTTONUP:
        if (GetWindowLongPtrW(w, GWLP_USERDATA) == NOTICE) {
            POINT p;
            p.x = (short)LOWORD(lp);
            p.y = (short)HIWORD(lp);
            if (PtInRect(&cross, p)) live_note_press();
        }
        return 0;
    }
    return DefWindowProcW(w, m, wp, lp);
}

static HWND make(LONG_PTR kind, int x, int y, int w, int h, int radius) {
    HWND win = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED, CLASS_NAME,
                               L"OrthoStudio XP Live", WS_POPUP, x, y, w, h, NULL, NULL, module, NULL);
    if (!win) return NULL;
    SetWindowLongPtrW(win, GWLP_USERDATA, kind); /* before it is shown: its first drawing knows it */
    SetLayeredWindowAttributes(win, 0, ALPHA, LWA_ALPHA);
    SetWindowRgn(win, CreateRoundRectRgn(0, 0, w + 1, h + 1, 2 * radius, 2 * radius), FALSE);
    ShowWindow(win, SW_SHOWNOACTIVATE);
    UpdateWindow(win);
    return win;
}

static void gone(HWND *w) {
    if (*w) DestroyWindow(*w);
    *w = NULL;
}

static void on_top(HWND w) {
    if (w) SetWindowPos(w, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER);
}

/* Makes, updates or takes away the windows as X-Plane's thread wants them. */
static void sync(void) {
    set_thread_awareness_f set_awareness =
        (set_thread_awareness_f)(void *)user32("SetThreadDpiAwarenessContext");
    int show_panel, show_note, serial, french;
    RECT around;
    UINT dpi;
    wchar_t text[200];
    EnterCriticalSection(&lock);
    show_panel = want.panel;
    show_note = want.note;
    serial = want.note_serial;
    french = want.french;
    around = want.around;
    dpi = want.dpi;
    wcscpy(text, want.text);
    if (set_awareness && want.awareness) set_awareness(want.awareness);
    LeaveCriticalSection(&lock);
    (void)french; /* the cross says nothing: the Mac's tooltip has no counterpart here */

    if (!show_panel) gone(&panel);
    else if (panel) {
        on_top(panel);
        InvalidateRect(panel, NULL, FALSE);
        UpdateWindow(panel);
    } else {
        int w, h;
        fonts_for(dpi);
        w = px(PANEL_W);
        h = px(PANEL_H);
        panel = make(LOADING, (around.left + around.right) / 2 - w / 2, (around.top + around.bottom) / 2 - h / 2, w,
                     h, px(12));
    }

    if (!show_note) gone(&note);
    else if (!note || note_made != serial) {
        /* small, in the top right corner of X-Plane's window, below its menu bar's band; as wide as
           its words */
        HDC screen = GetDC(NULL);
        SIZE size = {0, 0};
        int text_w, w, h;
        gone(&note);
        fonts_for(dpi);
        if (screen) {
            HGDIOBJ before = SelectObject(screen, note_font);
            GetTextExtentPoint32W(screen, text, (int)wcslen(text), &size);
            SelectObject(screen, before);
            ReleaseDC(NULL, screen);
        }
        text_w = size.cx + px(8);
        if (text_w < px(270)) text_w = px(270);
        if (text_w > px(560)) text_w = px(560);
        w = px(14) + text_w + px(34);
        h = px(NOTE_H);
        cross.left = w - px(32);
        cross.top = px(9);
        cross.right = cross.left + px(26);
        cross.bottom = cross.top + px(26);
        note = make(NOTICE, around.right - w - px(16), around.top + px(48), w, h, px(10));
        note_made = serial;
    }
}

static DWORD WINAPI windows_thread(LPVOID ready) {
    WNDCLASSEXW wc;
    MSG m;
    PeekMessageW(&m, NULL, WM_USER, WM_USER, PM_NOREMOVE); /* its message queue, before anything is posted */
    /* this code, held loaded while the thread runs (let go of as it ends) */
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, (LPCWSTR)(void *)window_proc, &module);
    memset(&wc, 0, sizeof wc);
    wc.cbSize = sizeof wc;
    wc.lpfnWndProc = window_proc;
    wc.hInstance = module;
    wc.hCursor = LoadCursorW(NULL, (LPCWSTR)IDC_ARROW);
    wc.lpszClassName = CLASS_NAME;
    klass = RegisterClassExW(&wc);
    SetTimer(NULL, 0, 1000, NULL); /* the windows back on top, every second */
    SetEvent((HANDLE)ready);
    while (GetMessageW(&m, NULL, 0, 0) > 0) {
        if (m.hwnd == NULL && m.message == WM_LIVE_SYNC) {
            sync();
            continue;
        }
        if (m.hwnd == NULL && m.message == WM_TIMER) {
            on_top(panel);
            on_top(note);
            continue;
        }
        TranslateMessage(&m);
        DispatchMessageW(&m);
    }
    gone(&panel);
    gone(&note);
    forget_fonts();
    if (klass) UnregisterClassW(CLASS_NAME, module);
    klass = 0;
    if (module && module != GetModuleHandleW(NULL)) FreeLibraryAndExitThread(module, 0); /* a plugin */
    return 0;                                                                         /* a test program */
}
