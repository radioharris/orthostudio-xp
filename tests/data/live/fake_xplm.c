/* A stand-in for X-Plane around native/xplane_plugin/live_gate.c and the test pilot
   (native/xplane_test_pilot/test_pilot.c): the XPLM functions they use and, unless built with
   -DNO_MAIN, a main() that does what the command line says, in order:
   - "102:0": send it a message (PLANE_LOADED for the user's aircraft), and say how long it took;
   - "loop": call its flight loop, as X-Plane does every second of a flight;
   - "frames:5000": call its flight loops every 10 ms for 5 s, as X-Plane's frames do;
   - "click": click the cross of its notice's panel (not built with -DNO_CLICK, for the pilot);
   - "sleep:100": wait 100 ms;
   - "set:sim/private/controls/reno/tex_res=5": set a dataref (X-Plane's texture quality).
   Its datarefs keep what is set into them; X-Plane's private ones ("sim/private/...") are not
   there until a "set:" makes them, as X-Plane's own are not when its plugins start; X-Plane's running time is the wall clock's since the
   start, its position what XPLMPlaceUserAtLocation said. The commands run are printed
   ("COMMAND sim/operation/quit"). */

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#define EXPORT __declspec(dllexport) /* built as a fake XPLM_64.dll with -DNO_MAIN */
#define SEP "\\"
#else
#define EXPORT
#define SEP "/"
#endif
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double lat, lon, ele, run_clock; /* run_clock: only its address, the wall clock read */
static double origin_lat, origin_lon; /* where local positions are counted from: the first place */
static int origin_set = 0, quit_asked = 0;
static FILE *log_file = NULL; /* fake_log_to: what plugins say goes there, as X-Plane's Log.txt */
#define SLOTS 128
static char slot_names[SLOTS][96];
static double slots[SLOTS];

static double wall_seconds(void) {
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

static double started_at = -1.0;

EXPORT void *XPLMFindDataRef(const char *name) {
    int i;
    if (strstr(name, "latitude")) return &lat;
    if (strstr(name, "longitude")) return &lon;
    if (strcmp(name, "sim/flightmodel/position/elevation") == 0) return &ele;
    if (strcmp(name, "sim/time/total_running_time_sec") == 0) return &run_clock;
    for (i = 0; i < SLOTS; i++) {
        if (!slot_names[i][0]) {
            if (strncmp(name, "sim/private/", 12) == 0) return NULL; /* not made by a "set:" */
            snprintf(slot_names[i], sizeof slot_names[i], "%s", name);
        }
        if (strcmp(slot_names[i], name) == 0) return &slots[i];
    }
    return NULL;
}

/* A dataref made if need be, whatever its name, and set: the command line's "set:". */
EXPORT void fake_set(const char *name, double value) {
    int i;
    for (i = 0; i < SLOTS; i++) {
        if (!slot_names[i][0]) snprintf(slot_names[i], sizeof slot_names[i], "%s", name);
        if (strcmp(slot_names[i], name) == 0) {
            slots[i] = value;
            return;
        }
    }
}

EXPORT double XPLMGetDatad(void *ref) {
    if (ref == &run_clock) {
        if (started_at < 0) started_at = wall_seconds();
        return wall_seconds() - started_at;
    }
    return ref ? *(double *)ref : 0.0;
}

EXPORT float XPLMGetDataf(void *ref) { return (float)XPLMGetDatad(ref); }

EXPORT int XPLMGetDatai(void *ref) { return (int)XPLMGetDatad(ref); }

static void set_origin(void) {
    if (!origin_set) origin_lat = lat, origin_lon = lon, origin_set = 1;
}

EXPORT void XPLMSetDatad(void *ref, double value) {
    double *slot = (double *)ref;
    if (!ref || ref == &run_clock) return;
    *slot = value;
    if (slot >= slots && slot < slots + SLOTS) { /* the aircraft moved by its local position */
        const char *name = slot_names[slot - slots];
        set_origin();
        if (strcmp(name, "sim/flightmodel/position/local_x") == 0)
            lon = origin_lon + value / (111195.0 * cos(origin_lat * 3.14159265358979 / 180.0));
        else if (strcmp(name, "sim/flightmodel/position/local_z") == 0)
            lat = origin_lat - value / 111195.0;
        else if (strcmp(name, "sim/flightmodel/position/local_y") == 0)
            ele = value;
    }
}

EXPORT void XPLMSetDataf(void *ref, float value) { XPLMSetDatad(ref, value); }

EXPORT void XPLMSetDatai(void *ref, int value) { XPLMSetDatad(ref, value); }

EXPORT void XPLMSetDatavf(void *ref, float *values, int offset, int count) {
    if (count > 0 && offset == 0) XPLMSetDatad(ref, values[0]);
}

EXPORT void XPLMSetDatavi(void *ref, int *values, int offset, int count) {
    if (count > 0 && offset == 0) XPLMSetDatad(ref, values[0]);
}

EXPORT void *XPLMFindCommand(const char *name) { return (void *)name; } /* only ever a literal */

EXPORT void XPLMCommandOnce(void *command) {
    const char *name = command ? (const char *)command : "?";
    if (strcmp(name, "sim/operation/quit") == 0) quit_asked = 1;
    printf("COMMAND %s\n", name);
    fflush(stdout);
}

/* Metres east, up, south of the first place: enough for a path of a few minutes. */
EXPORT void XPLMWorldToLocal(double at_lat, double at_lon, double alt, double *x, double *y, double *z) {
    set_origin();
    *x = (at_lon - origin_lon) * 111195.0 * cos(origin_lat * 3.14159265358979 / 180.0);
    *y = alt;
    *z = -(at_lat - origin_lat) * 111195.0;
}

EXPORT void XPLMPlaceUserAtLocation(double at_lat, double at_lon, float metres, float heading, float speed) {
    (void)heading;
    (void)speed;
    lat = at_lat;
    lon = at_lon;
    ele = metres;
    origin_set = 0;
    set_origin();
}

/* For fake_xplane.c, the stand-in for X-Plane itself. */
EXPORT void fake_elevation(double metres) { ele = metres; }

EXPORT int fake_quit_asked(void) { return quit_asked; }

EXPORT void fake_log_to(const char *path) {
#ifdef _WIN32
    wchar_t w[1200];
    if (MultiByteToWideChar(CP_UTF8, 0, path, -1, w, 1200)) log_file = _wfopen(w, L"ab");
#else
    log_file = fopen(path, "ab");
#endif
}

EXPORT void XPLMControlCamera(int how_long, void *control, void *refcon) {
    (void)how_long;
    (void)control;
    (void)refcon;
}

EXPORT void XPLMDontControlCamera(void) {}

/* X-Plane's folder: $FAKE_XPLANE with its separator, or none; in UTF-8, as X-Plane gives it (on
   Windows the variable read as UTF-16, its own code page losing what it lacks). */
EXPORT void XPLMGetSystemPath(char *out) {
#ifdef _WIN32
    wchar_t root[1024];
    DWORD n = GetEnvironmentVariableW(L"FAKE_XPLANE", root, 1024);
    out[0] = 0;
    if (n > 0 && n < 1024 && WideCharToMultiByte(CP_UTF8, 0, root, -1, out, 1000, NULL, NULL) > 0)
        strcat(out, SEP);
    else
        out[0] = 0;
#else
    const char *root = getenv("FAKE_XPLANE");
    if (root && *root) snprintf(out, 1024, "%s" SEP, root);
    else out[0] = 0;
#endif
}

EXPORT void XPLMEnableFeature(const char *feature, int enable) {
    (void)feature;
    (void)enable;
}

EXPORT void XPLMDebugString(const char *s) {
    if (log_file) { /* X-Plane's Log.txt */
        fputs(s, log_file);
        fflush(log_file);
        return;
    }
    printf("LOG %s", s);
    fflush(stdout);
}

typedef float (*XPLMFlightLoop_f)(float, float, int, void *);
#define LOOPS 4 /* the plugin's flight loops: every second, every frame */
static XPLMFlightLoop_f loop_cbs[LOOPS];
static void *loop_refs[LOOPS];

EXPORT void XPLMRegisterFlightLoopCallback(XPLMFlightLoop_f f, float interval, void *ref) {
    int i;
    (void)interval;
    for (i = 0; i < LOOPS; i++) {
        if (!loop_cbs[i]) {
            loop_cbs[i] = f;
            loop_refs[i] = ref;
            return;
        }
    }
}

EXPORT void XPLMUnregisterFlightLoopCallback(XPLMFlightLoop_f f, void *ref) {
    int i;
    (void)ref;
    for (i = 0; i < LOOPS; i++)
        if (loop_cbs[i] == f) loop_cbs[i] = NULL;
}

/* For a program that loads the plugin as X-Plane does (xpl_host.c, fake_xplane.c): where the
   aircraft is, and X-Plane's flight loops called once, given the time since the call before (the
   test pilot's frame time); 1 if one was. */
EXPORT void fake_position(double at_lat, double at_lon) {
    lat = at_lat;
    lon = at_lon;
}

EXPORT int fake_loops(int counter) {
    static double last = -1.0;
    double now = wall_seconds();
    float since = last < 0 ? 0.0f : (float)(now - last);
    int j, called = 0;
    last = now;
    for (j = 0; j < LOOPS; j++) {
        if (loop_cbs[j]) {
            loop_cbs[j](since, since, counter, loop_refs[j]);
            called = 1;
        }
    }
    return called;
}

#ifndef NO_MAIN
#ifndef _WIN32
#include <unistd.h>
#endif
#ifndef NO_CLICK /* built around the test pilot, which has no notice */
void live_note_press(void);
#endif
int XPluginStart(char *, char *, char *);
int XPluginEnable(void);
void XPluginDisable(void);
void XPluginStop(void);
void XPluginReceiveMessage(int, int, void *);

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

int main(int argc, char **argv) {
    char name[256], sig[256], desc[256];
    int i;
    if (argc < 3) return 2;
    fake_position(atof(argv[1]), atof(argv[2]));
    XPluginStart(name, sig, desc);
    XPluginEnable();
    printf("READY\n");
    fflush(stdout);
    for (i = 3; i < argc; i++) {
        int msg, param;
        double a, b;
        if (strcmp(argv[i], "loop") == 0) {
            printf("LOOP %s\n", fake_loops(i) ? "called" : "none");
            fflush(stdout);
            continue;
        }
        if (sscanf(argv[i], "frames:%d", &param) == 1) {
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
#ifndef NO_CLICK
        if (strcmp(argv[i], "click") == 0) {
            live_note_press();
            continue;
        }
#endif
        if (strncmp(argv[i], "set:", 4) == 0 && strchr(argv[i], '=')) {
            char ref[96];
            const char *eq = strchr(argv[i], '=');
            snprintf(ref, sizeof ref, "%.*s", (int)(eq - argv[i] - 4), argv[i] + 4);
            fake_set(ref, atof(eq + 1));
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
        XPluginReceiveMessage(0, msg, (void *)(intptr_t)param);
        b = seconds();
        printf("MSG %d %d %.3f\n", msg, param, b - a);
        fflush(stdout);
    }
    XPluginDisable();
    XPluginStop();
    return 0;
}
#endif
