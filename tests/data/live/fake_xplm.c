/* A stand-in for X-Plane around native/xplane_plugin/live_gate.c: the XPLM functions it uses and,
   unless built with -DNO_MAIN, a main() that does what the command line says, in order:
   - "102:0": send it a message (PLANE_LOADED for the user's aircraft), and say how long it took;
   - "loop": call its flight loop, as X-Plane does every second of a flight;
   - "click": click the cross of its notice's panel;
   - "sleep:100": wait 100 ms. */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double lat, lon;

void *XPLMFindDataRef(const char *name) {
    if (strstr(name, "latitude")) return &lat;
    if (strstr(name, "longitude")) return &lon;
    return NULL;
}

double XPLMGetDatad(void *ref) { return ref ? *(double *)ref : 0.0; }

/* X-Plane's folder: $FAKE_XPLANE with its separator, or none. */
void XPLMGetSystemPath(char *out) {
    const char *root = getenv("FAKE_XPLANE");
    if (root && *root) snprintf(out, 1024, "%s/", root);
    else out[0] = 0;
}

void XPLMEnableFeature(const char *feature, int enable) {
    (void)feature;
    (void)enable;
}

void XPLMDebugString(const char *s) {
    printf("LOG %s", s);
    fflush(stdout);
}

typedef float (*XPLMFlightLoop_f)(float, float, int, void *);
#define LOOPS 4 /* the plugin's flight loops: every second, every frame */
static XPLMFlightLoop_f loop_cbs[LOOPS];
static void *loop_refs[LOOPS];

void XPLMRegisterFlightLoopCallback(XPLMFlightLoop_f f, float interval, void *ref) {
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

void XPLMUnregisterFlightLoopCallback(XPLMFlightLoop_f f, void *ref) {
    int i;
    (void)ref;
    for (i = 0; i < LOOPS; i++)
        if (loop_cbs[i] == f) loop_cbs[i] = NULL;
}

#ifndef NO_MAIN
#include <unistd.h>
void live_note_press(void);
int XPluginStart(char *, char *, char *);
int XPluginEnable(void);
void XPluginDisable(void);
void XPluginStop(void);
void XPluginReceiveMessage(int, int, void *);

int main(int argc, char **argv) {
    char name[256], sig[256], desc[256];
    int i;
    if (argc < 3) return 2;
    lat = atof(argv[1]);
    lon = atof(argv[2]);
    XPluginStart(name, sig, desc);
    XPluginEnable();
    printf("READY\n");
    fflush(stdout);
    for (i = 3; i < argc; i++) {
        int msg, param;
        struct timespec a, b;
        if (strcmp(argv[i], "loop") == 0) {
            int j, called = 0;
            for (j = 0; j < LOOPS; j++) {
                if (loop_cbs[j]) {
                    loop_cbs[j](1.0f, 1.0f, i, loop_refs[j]);
                    called = 1;
                }
            }
            printf("LOOP %s\n", called ? "called" : "none");
            fflush(stdout);
            continue;
        }
        if (strcmp(argv[i], "click") == 0) {
            live_note_press();
            continue;
        }
        if (sscanf(argv[i], "sleep:%d", &param) == 1) {
            usleep(param * 1000);
            continue;
        }
        if (sscanf(argv[i], "%d:%d", &msg, &param) != 2) return 2;
        clock_gettime(CLOCK_MONOTONIC, &a);
        XPluginReceiveMessage(0, msg, (void *)(intptr_t)param);
        clock_gettime(CLOCK_MONOTONIC, &b);
        printf("MSG %d %d %.3f\n", msg, param, (double)(b.tv_sec - a.tv_sec) + (b.tv_nsec - a.tv_nsec) / 1e9);
        fflush(stdout);
    }
    XPluginDisable();
    XPluginStop();
    return 0;
}
#endif
