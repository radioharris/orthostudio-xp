/* OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
 * Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE. */

/*
 * OrthoStudio XP Live's X-Plane plugin: while X-Plane loads a flight, it waits until the ortho around the
 * start is built. AutoOrtho gets that wait from being a filesystem. OrthoStudio XP Live serves real
 * files, so it asks X-Plane from the inside.
 *
 * X-Plane loads a flight's aircraft before its scenery, and tells plugins as it goes: PLANE_LOADED,
 * then FMOD_BANK_LOADED and LIVERY_LOADED. It reads no scenery before these
 * (docs/specs/live/hold-test.md).
 * - At PLANE_LOADED, the plugin tells the streamer where the flight starts, so that it builds while
 *   the aircraft loads.
 * - At the radio's FMOD_BANK_LOADED and at LIVERY_LOADED, it waits for the streamer to say that
 *   every texture X-Plane will read there is built. It asks every ASK_EVERY_S over UDP on
 *   127.0.0.1:LIVE_PORT.
 * - A new flight with the same aircraft loads no aircraft, so none of these messages come. There,
 *   the plugin waits at message 115 (1), which is undocumented and came first in the loading of the
 *   first flight. It does so only once a flight has been loaded: at the first flight it would hold
 *   the aircraft's loading too, which can go on while the streamer builds.
 *
 * Every message X-Plane sends is noted in Log.txt ("OrthoStudio XP Live: message 115 (1)"). X-Plane's
 * main thread writes Log.txt, so the notes fall in order among X-Plane's own lines, such as
 * "Init dat_p0" and the DSF loads. X-Plane sends nothing when it starts reading the scenery.
 *
 * While it waits, X-Plane's window is not redrawn: at a flight change it still shows the Flight
 * Configuration screen. So the plugin shows its own loading window (live_panel.m) with what
 * the streamer says: where, and how many textures are left.
 *
 * It never holds X-Plane for nothing:
 * - no streamer: nothing answers and X-Plane goes on;
 * - the streamer stops while X-Plane waits (an error, Ctrl-C): its port closes, X-Plane goes on at
 *   once, and Log.txt says so;
 * - a livery change in flight: the streamer says go at once;
 * - no time limit otherwise, as long as the streamer answers: a start with sharp zooms takes more
 *   than the five minutes it once allowed (a user, 2026-09-27). The streamer itself lets X-Plane go
 *   when nothing has downloaded for a minute (no internet), and Stop in the Live window does too.
 *
 * In flight, every second, it asks whether the streamer has a notice for the pilot ("LIVE1 NOTE id
 * text": no mesh here, the region to install), which a small panel shows with its cross
 * (live_panel.m); "LIVE1 NONE": none. It offered a reload of the scenery too, for the 8192 px
 * textures built after a start, until a test showed that X-Plane's reload does not read again the
 * textures it holds (2026-09-27).
 *
 * orthostudio/live/plugin.py builds it against X-Plane's own XPLM framework. It declares the few XPLM functions
 * it uses itself, so the SDK is not needed.
 */

#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>

#define PLUGIN_API __attribute__((visibility("default")))

typedef void *XPLMDataRef;
typedef int XPLMPluginID;
typedef float (*XPLMFlightLoop_f)(float inElapsedSinceLastCall, float inElapsedTimeSinceLastFlightLoop,
                                  int inCounter, void *inRefcon);
extern XPLMDataRef XPLMFindDataRef(const char *inDataRefName);
extern double XPLMGetDatad(XPLMDataRef inDataRef);
extern void XPLMDebugString(const char *inString);
extern void XPLMRegisterFlightLoopCallback(XPLMFlightLoop_f inFlightLoop, float inInterval, void *inRefcon);
extern void XPLMUnregisterFlightLoopCallback(XPLMFlightLoop_f inFlightLoop, void *inRefcon);

#define XPLM_PLUGIN_XPLANE 0
#define XPLM_MSG_PLANE_LOADED 102
#define XPLM_MSG_LIVERY_LOADED 108
#define XPLM_MSG_FMOD_BANK_LOADED 112
#define XPLM_RADIO_BANK 1
#define XPLM_MSG_115 115 /* undocumented: (1) as a flight starts loading, (0) when leaving a flight for the menus */

/* Settable at build time (-D...), which the tests use. */
#ifndef LIVE_PORT
#define LIVE_PORT 49250
#endif
#ifndef ASK_EVERY_S
#define ASK_EVERY_S 0.25
#endif
#ifndef NO_STREAMER_S
#define NO_STREAMER_S 1.0 /* nothing answered at all: no streamer */
#endif
#ifndef SILENT_S
#define SILENT_S 5.0 /* it answered, then stopped */
#endif
#define NOTE_EVERY_S 1.0f /* in flight: whether the streamer has a notice */

void live_panel_show(const char *what, const char *progress, double fraction); /* live_panel.m */
void live_panel_hide(void);
void live_note_show(const char *text, int french); /* the notice, with its cross */
void live_note_hide(void);
int live_note_closed(void); /* 1: its cross clicked since the last call */

static int sock = -1;
static int flights = 0; /* flights whose aircraft has loaded */
static XPLMDataRef lat_ref, lon_ref;
static int noted = 0, closed_note = 0; /* the notice shown (its id), and the last one closed */
static int french = 0; /* the Live window's language, which each answer says: its words in X-Plane */
static char note[160]; /* its words */

static double now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void say(const char *fmt, ...) {
    char text[300], line[330];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    snprintf(line, sizeof line, "OrthoStudio XP Live: %s\n", text);
    XPLMDebugString(line);
}

static int open_socket(void) {
    struct sockaddr_in addr;
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    if (s < 0) return -1;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons(LIVE_PORT);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    /* Connected: a port nobody listens on shows as ECONNREFUSED at once. */
    if (connect(s, (struct sockaddr *)&addr, sizeof addr) < 0) {
        close(s);
        return -1;
    }
    return s;
}

static void drain(void) {
    char buf[256];
    while (recv(sock, buf, sizeof buf, MSG_DONTWAIT) >= 0) {
    }
}

/* What the streamer says while X-Plane waits: "LIVE1 WAIT n=12 p=450 s=23 mbs=15.2 lang=en at=LFBO runway 14L"
   is 12 textures left, 45.0 % of the work done, 23 s since the start was known, downloading at
   15.2 MB/s, the Live window's language (the words shown take it), and where (the rest of the
   line, in that language). */
struct state {
    int left, permille, secs;
    double mbs;
    char where[128];
};

static void parse_wait(char *p, struct state *st) {
    while (*p) {
        while (*p == ' ') p++;
        if (strncmp(p, "at=", 3) == 0) {
            if (p[3]) snprintf(st->where, sizeof st->where, "%s", p + 3);
            return;
        }
        if (sscanf(p, "n=%d", &st->left) != 1 && sscanf(p, "p=%d", &st->permille) != 1 &&
            sscanf(p, "s=%d", &st->secs) != 1 && sscanf(p, "mbs=%lf", &st->mbs) != 1 && *p >= '0' && *p <= '9')
            st->left = atoi(p); /* "LIVE1 WAIT 12": the count alone */
        while (*p && *p != ' ') p++;
    }
}

/* Sends `line` and waits up to `wait_s` for an answer. Returns 1 for go, 0 for wait, -1 for no
   answer yet, -2 if nothing listens (no streamer). */
/* "lang=fr" or "lang=en" in an answer of the streamer: the Live window's language. */
static void language_of(const char *answer) {
    const char *l = strstr(answer, " lang=");
    if (l) french = strncmp(l + 6, "fr", 2) == 0;
}

static int ask(const char *line, double wait_s, struct state *st) {
    char buf[256];
    struct timeval tv;
    double until = now() + wait_s;
    drain();
    send(sock, line, strlen(line), 0);
    for (;;) {
        double rest = until - now();
        ssize_t n;
        if (rest <= 0) return -1;
        tv.tv_sec = (time_t)rest;
        tv.tv_usec = (suseconds_t)((rest - (double)tv.tv_sec) * 1e6);
        if (tv.tv_sec == 0 && tv.tv_usec == 0) tv.tv_usec = 1000;
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        n = recv(sock, buf, sizeof buf - 1, 0);
        if (n < 0) return errno == ECONNREFUSED ? -2 : -1;
        buf[n] = 0;
        if (sscanf(buf, "LIVE1 GO %d", &st->left) == 1) return 1;
        if (strncmp(buf, "LIVE1 WAIT ", 11) == 0) {
            language_of(buf);
            parse_wait(buf + 11, st);
            return 0;
        }
    }
}

static int position(double *lat, double *lon) {
    *lat = XPLMGetDatad(lat_ref);
    *lon = XPLMGetDatad(lon_ref);
    return !(*lat == 0.0 && *lon == 0.0); /* unset while X-Plane starts up */
}

static void tell_start(void) {
    char line[96];
    double lat, lon;
    if (sock < 0 || !position(&lat, &lon)) return;
    snprintf(line, sizeof line, "LIVE1 LOAD %.6f %.6f", lat, lon);
    drain();
    send(sock, line, strlen(line), 0);
}

static void show(const struct state *st) {
    char what[200], progress[100];
    if (french) {
        char rate[32], *dot;
        snprintf(rate, sizeof rate, "%.1f", st->mbs);
        if ((dot = strchr(rate, '.')) != NULL) *dot = ','; /* 15,2 Mo/s */
        snprintf(what, sizeof what, "Chargement de l'ortho autour de %s", st->where);
        snprintf(progress, sizeof progress, "%d textures à construire  -  %d s  -  %s Mo/s", st->left, st->secs, rate);
    } else {
        snprintf(what, sizeof what, "Loading the ortho around %s", st->where);
        snprintf(progress, sizeof progress, "%d textures to build  -  %d s  -  %.1f MB/s", st->left, st->secs, st->mbs);
    }
    live_panel_show(what, progress, st->permille / 1000.0);
}

static void hold(const char *at) {
    char line[96];
    struct state st = {-1, 0, 0, 0.0, ""};
    const char *why = strcmp(at, "the start of a new flight") == 0 ? "flight" : "aircraft";
    double lat, lon, start, answered = 0, t;
    int told = 0, r;
    if (sock < 0 || !position(&lat, &lon)) return;
    snprintf(line, sizeof line, "LIVE1 HOLD %.6f %.6f %s", lat, lon, why);
    snprintf(st.where, sizeof st.where, "%.3f %.3f", lat, lon);
    start = now();
    for (;;) {
        double asked = now();
        r = ask(line, ASK_EVERY_S, &st);
        t = now();
        if (r == 1) {
            if (told) {
                say("the ortho is built after %.0f s: X-Plane goes on", t - start);
                live_panel_hide();
            }
            return;
        }
        if (r == -2) { /* nothing listens: no streamer, or it stopped */
            if (answered > 0) say("the streamer stopped: X-Plane goes on");
            break;
        }
        if (r == 0) {
            answered = t;
            if (!told) {
                say("X-Plane waits at %s until the ortho around %s is built (%d textures to go)", at, st.where, st.left);
                told = 1;
            }
            show(&st);
            if (t - asked < ASK_EVERY_S) usleep((useconds_t)((ASK_EVERY_S - (t - asked)) * 1e6));
        }
        if (answered == 0 && t - start > NO_STREAMER_S) break;
        if (answered > 0 && t - answered > SILENT_S) {
            say("the streamer stopped answering: X-Plane goes on");
            break;
        }
    }
    live_panel_hide();
}

#define FRAME_GAP_S 5.0 /* longer between two frames: X-Plane loaded or was held, no stutter */
static double frame_last = 0.0, frame_max = 0.0; /* the longest frame of the second */
static int frames = 0;

/* Every frame (it returns -1): the time since the one before, the longest of each second kept for
   the ask, which tells the streamer: X-Plane's stutters, next to what Live was doing (a user saw
   some while Live built, 2026-09-28). */
static float every_frame(float since_call, float since_loop, int counter, void *refcon) {
    double t = now(), gap = frame_last > 0.0 ? t - frame_last : 0.0;
    (void)since_call;
    (void)since_loop;
    (void)counter;
    (void)refcon;
    frame_last = t;
    if (gap > 0.0 && gap < FRAME_GAP_S) {
        if (gap > frame_max) frame_max = gap;
        frames++;
    }
    return -1.0f;
}

/* Every second of a flight: whether the streamer has a notice ("LIVE1 NOTE id text"), and whether
   its cross was clicked. The answers to the last ask are read without waiting. The ask says the
   second's frames: "LIVE1 ASK max=123 n=45", the longest in ms and how many. */
static float every_second(float since_call, float since_loop, int counter, void *refcon) {
    char buf[256];
    ssize_t n;
    int id, at;
    (void)since_call;
    (void)since_loop;
    (void)counter;
    (void)refcon;
    if (sock < 0 || flights == 0) return NOTE_EVERY_S;
    if (live_note_closed() && noted) closed_note = noted; /* its cross */
    while ((n = recv(sock, buf, sizeof buf - 1, MSG_DONTWAIT)) >= 0) {
        buf[n] = 0;
        at = 0;
        language_of(buf);
        if (strncmp(buf, "LIVE1 NONE", 10) == 0) {
            noted = 0;
        } else if (sscanf(buf, "LIVE1 NOTE %d %n", &id, &at) == 1 && at > 0) {
            if (strncmp(buf + at, "lang=", 5) == 0 && strlen(buf + at) > 8) at += 8; /* "lang=fr " */
            if (id != noted && id != closed_note) say("%s", buf + at);
            noted = id;
            snprintf(note, sizeof note, "%s", buf + at);
        }
    }
    if (noted && noted != closed_note)
        live_note_show(note, french);
    else
        live_note_hide();
    snprintf(buf, sizeof buf, "LIVE1 ASK max=%d n=%d", (int)(frame_max * 1000.0 + 0.5), frames);
    frame_max = 0.0;
    frames = 0;
    send(sock, buf, strlen(buf), 0);
    return NOTE_EVERY_S;
}

PLUGIN_API int XPluginStart(char *outName, char *outSig, char *outDesc) {
    strcpy(outName, "OrthoStudio XP Live");
    strcpy(outSig, "orthostudio.live");
    strcpy(outDesc, "Makes X-Plane wait, while a flight loads, until OrthoStudio XP Live has built the ortho around the start.");
    lat_ref = XPLMFindDataRef("sim/flightmodel/position/latitude");
    lon_ref = XPLMFindDataRef("sim/flightmodel/position/longitude");
    return 1;
}

PLUGIN_API void XPluginStop(void) {}

PLUGIN_API int XPluginEnable(void) {
    sock = open_socket();
    if (sock < 0) say("no UDP socket: X-Plane will not wait for the ortho");
    XPLMRegisterFlightLoopCallback(every_second, NOTE_EVERY_S, NULL);
    XPLMRegisterFlightLoopCallback(every_frame, -1.0f, NULL);
    return 1;
}

PLUGIN_API void XPluginDisable(void) {
    XPLMUnregisterFlightLoopCallback(every_second, NULL);
    XPLMUnregisterFlightLoopCallback(every_frame, NULL);
    live_note_hide();
    if (sock >= 0) close(sock);
    sock = -1;
}

PLUGIN_API void XPluginReceiveMessage(XPLMPluginID inFrom, int inMsg, void *inParam) {
    intptr_t param = (intptr_t)inParam;
    if (inFrom != XPLM_PLUGIN_XPLANE) return;
    say("message %d (%ld)", inMsg, (long)param);
    if (inMsg == XPLM_MSG_PLANE_LOADED && param == 0) {
        flights++;
        tell_start();
    } else if (inMsg == XPLM_MSG_FMOD_BANK_LOADED && param == XPLM_RADIO_BANK)
        hold("the radio sounds");
    else if (inMsg == XPLM_MSG_LIVERY_LOADED && param == 0)
        hold("the livery");
    else if (inMsg == XPLM_MSG_115 && param == 1 && flights > 0)
        hold("the start of a new flight");
}
