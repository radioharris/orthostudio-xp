/* A stand-in for X-Plane around plugin/xso_gate.c: the three XPLM functions it uses and, unless
   built with -DNO_MAIN, a main() that sends it the messages given on the command line
   ("102:0" is PLANE_LOADED for the user's aircraft) and says how long each call took. */

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

void XPLMDebugString(const char *s) {
    printf("LOG %s", s);
    fflush(stdout);
}

#ifndef NO_MAIN
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
