/* X-Plane's way of loading a plugin, for the Windows and Linux plugins built ahead
   (native/xplane_plugin/prebuilt): the plugin's file loaded as X-Plane loads it, its functions found
   by name, the XPLM functions it calls given by fake_xplm.c (built into this program and exported,
   -rdynamic, on Linux; a fake XPLM_64.dll on Windows, which the plugin imports from). The command
   line: the plugin's file, a latitude and a longitude, then what fake_xplm.c's main() takes:
   "102:0", "loop", "frames:5000", "sleep:100" ("click" needs the plugin's own code). On Windows, "window" first
   opens a window of this program's main thread, as X-Plane's, and a thread asks Windows every
   250 ms whether it responds (IsHungAppWindow): "HUNG" if ever it does not, while the plugin
   holds the thread. On Linux, built with -DHOST_X11 -lX11, "window" opens one as X-Plane's on the
   display, which says it answers the window manager's pings (_NET_WM_PING). */

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <dlfcn.h>
#include <unistd.h>
#endif
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

void fake_position(double lat, double lon); /* fake_xplm.c */
int fake_loops(int counter);

typedef int (*start_f)(char *, char *, char *);
typedef int (*enable_f)(void);
typedef void (*plain_f)(void);
typedef void (*message_f)(int, int, void *);

static double seconds(void) {
#ifdef _WIN32
    LARGE_INTEGER hz, t;
    QueryPerformanceFrequency(&hz);
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart / (double)hz.QuadPart;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + ts.tv_nsec / 1e9;
#endif
}

#ifdef _WIN32
static HWND xplane_window;
static volatile LONG watching = 1, hung = 0;

static DWORD WINAPI watch(LPVOID unused) {
    (void)unused;
    while (watching) {
        if (IsHungAppWindow(xplane_window) && !InterlockedExchange(&hung, 1)) {
            printf("HUNG\n");
            fflush(stdout);
        }
        Sleep(250);
    }
    return 0;
}

/* A window of the main thread, as X-Plane's, and the thread that watches it. */
static void open_window(void) {
    WNDCLASSW wc;
    MSG m;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.lpszClassName = L"FakeXPlane";
    wc.hbrBackground = (HBRUSH)(COLOR_APPWORKSPACE + 1);
    RegisterClassW(&wc);
    xplane_window = CreateWindowW(L"FakeXPlane", L"X-Plane (stand-in)", WS_OVERLAPPEDWINDOW | WS_VISIBLE, 100,
                                  100, 1200, 700, NULL, NULL, wc.hInstance, NULL);
    while (PeekMessageW(&m, NULL, 0, 0, PM_REMOVE)) DispatchMessageW(&m);
    CreateThread(NULL, 0, watch, NULL, 0, NULL);
    printf("WINDOW %s\n", xplane_window ? "open" : "none");
    fflush(stdout);
}
#endif

#if !defined(_WIN32) && defined(HOST_X11)
#include <X11/Xatom.h>
#include <X11/Xlib.h>

/* A window of this process, as X-Plane's: its pid, and the pings it says it answers. */
static void open_window(void) {
    Display *dpy = XOpenDisplay(NULL);
    Window win;
    Atom protocols[2];
    long pid = (long)getpid();
    if (!dpy) {
        printf("WINDOW none\n");
        return;
    }
    win = XCreateSimpleWindow(dpy, DefaultRootWindow(dpy), 100, 100, 1200, 700, 0, 0, 0x808080);
    XChangeProperty(dpy, win, XInternAtom(dpy, "_NET_WM_PID", False), XA_CARDINAL, 32, PropModeReplace,
                    (unsigned char *)&pid, 1);
    protocols[0] = XInternAtom(dpy, "WM_DELETE_WINDOW", False);
    protocols[1] = XInternAtom(dpy, "_NET_WM_PING", False);
    XSetWMProtocols(dpy, win, protocols, 2);
    XStoreName(dpy, win, "X-Plane (stand-in)");
    XMapWindow(dpy, win);
    XSync(dpy, False);
    printf("WINDOW open %lu\n", (unsigned long)win);
    fflush(stdout);
}
#endif

static void *find(void *plugin, const char *name) {
#ifdef _WIN32
    return (void *)GetProcAddress((HMODULE)plugin, name);
#else
    return dlsym(plugin, name);
#endif
}

int main(int argc, char **argv) {
    char name[256], sig[256], desc[256];
    void *plugin;
    start_f start;
    enable_f enable;
    plain_f disable, stop;
    message_f message;
    int i;
    if (argc < 4) return 2;
#ifdef _WIN32
    plugin = (void *)LoadLibraryA(argv[1]);
#else
    plugin = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
#endif
    if (!plugin) {
#ifndef _WIN32
        printf("NOT LOADED %s\n", dlerror());
#else
        printf("NOT LOADED %lu\n", (unsigned long)GetLastError());
#endif
        return 3;
    }
    start = (start_f)find(plugin, "XPluginStart");
    enable = (enable_f)find(plugin, "XPluginEnable");
    disable = (plain_f)find(plugin, "XPluginDisable");
    stop = (plain_f)find(plugin, "XPluginStop");
    message = (message_f)find(plugin, "XPluginReceiveMessage");
    if (!start || !enable || !disable || !stop || !message) {
        printf("NO ENTRY POINT\n");
        return 4;
    }
    fake_position(atof(argv[2]), atof(argv[3]));
    if (start(name, sig, desc) != 1) return 5;
    printf("NAME %s\n", name);
    enable();
    printf("READY\n");
    fflush(stdout);
    for (i = 4; i < argc; i++) {
        int msg, param;
        double a;
        if (strcmp(argv[i], "loop") == 0) {
            printf("LOOP %s\n", fake_loops(i) ? "called" : "none");
            fflush(stdout);
            continue;
        }
#if defined(_WIN32) || defined(HOST_X11)
        if (strcmp(argv[i], "window") == 0) {
            open_window();
            continue;
        }
#endif
        if (sscanf(argv[i], "frames:%d", &param) == 1) { /* X-Plane's frames, every 10 ms */
            double until = seconds() + param / 1000.0;
            while (seconds() < until) {
                fake_loops(i);
#ifdef _WIN32
                Sleep(10);
#else
                usleep(10000);
#endif
            }
            continue;
        }
        if (sscanf(argv[i], "sleep:%d", &param) == 1) {
#ifdef _WIN32
            Sleep((DWORD)param);
#else
            usleep(param * 1000);
#endif
            continue;
        }
        if (sscanf(argv[i], "%d:%d", &msg, &param) != 2) return 2;
        a = seconds();
        message(0, msg, (void *)(intptr_t)param);
        printf("MSG %d %d %.3f\n", msg, param, seconds() - a);
        fflush(stdout);
    }
#ifdef _WIN32
    watching = 0;
    if (xplane_window) printf("RESPONDED %s\n", hung ? "no" : "always");
#endif
    disable();
    stop();
    return 0;
}
