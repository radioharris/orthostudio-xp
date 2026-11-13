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
 *   127.0.0.1:LIVE_PORT, with the flight's date, which sets the photos' season.
 * - A new flight with the same aircraft loads no aircraft, so none of these messages come: X-Plane
 *   12.4.3 calls no plugin at all between the click on Fly and the scenery's reading. Live pauses
 *   X-Plane itself there (orthostudio/live/pause.py) and shows its own loading window. Message 115,
 *   which the plugin once held at, is XPLM_MSG_WEATHER_DELIVERED (SDK 4.4.0): 12.4.4 sends it as a
 *   flight's weather is delivered, before its scenery, but at every change of weather too.
 * - On Windows, as X-Plane starts, it asks Windows not to "ghost" X-Plane's windows: while Live
 *   keeps X-Plane paused, a click on it would grey it as "Not Responding" and offer to close it
 *   (DisableProcessWindowsGhosting, which only the process itself can ask, 2026-11-01).
 *
 * Every message X-Plane sends is noted in Log.txt ("OrthoStudio XP Live: message 115 (1)"). X-Plane's
 * main thread writes Log.txt, so the notes fall in order among X-Plane's own lines, such as
 * "Init dat_p0" and the DSF loads. X-Plane sends nothing when it starts reading the scenery.
 *
 * While it waits, X-Plane's window is not redrawn: at a flight change it still shows the Flight
 * Configuration screen. So the plugin shows its own loading window with what the streamer says:
 * where, and how many textures are left. It is Cocoa on the Mac (live_panel.m), Win32 on Windows
 * (live_panel_win.c), X11 on Linux (live_panel_x11.c), each with the functions declared below. On
 * Windows the wait also tells the system, every ASK_EVERY_S, that X-Plane's thread is alive
 * (live_panel_tick): without it Windows greys X-Plane's window as "Not Responding" after 5 s.
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
 * text": no mesh here, the region to install), which a small panel shows with its cross (the
 * loading window's file); "LIVE1 NONE": none. It offered a reload of the scenery too, for the
 * 8192 px textures built after a start, until a test showed that X-Plane's reload does not read
 * again the textures it holds (2026-09-27).
 *
 * X-Plane's texture quality sets how sharp the levels it reads are, and the streamer builds no
 * sharper ones. The plugin reads it in X-Plane (QUALITY_REF) and tells the streamer: before each
 * hold at a start ("LIVE1 QUALITY 5"), and every second in flight, with the ask. X-Plane applies a
 * new quality at once, at "Resume flight", and reads again the textures round the aircraft
 * whose sharper levels it now reads, 3 to 6 s later. So when the quality rises in flight, the
 * plugin holds X-Plane there and then, with the same window and the same ways to let it go as at
 * a start, until the streamer has built those sharper levels, their night textures too ("LIVE1
 * SHARPEN lat lon 5"): held, X-Plane reads nothing, and reads them once let go (2026-11-13). A
 * lower quality holds nothing: X-Plane reads less sharp levels, which are there.
 *
 * On a Mac, orthostudio/live/plugin.py builds it with clang; the Windows and Linux plugins are built
 * ahead (tools/live/plugin_builds.py) and kept with the sources. It declares the few XPLM functions
 * it uses itself, so the SDK is not needed: X-Plane gives them to the plugin as it loads it on
 * Linux; on Windows an import library made from XPLM_64.def does, and on the Mac a stub of X-Plane's
 * XPLM framework made from the same list.
 */

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#endif
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifndef _WIN32
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

#ifdef _WIN32
#define PLUGIN_API __declspec(dllexport)
#else
#define PLUGIN_API __attribute__((visibility("default")))
#endif

typedef void *XPLMDataRef;
typedef int XPLMPluginID;
typedef float (*XPLMFlightLoop_f)(float inElapsedSinceLastCall, float inElapsedTimeSinceLastFlightLoop,
                                  int inCounter, void *inRefcon);
extern XPLMDataRef XPLMFindDataRef(const char *inDataRefName);
extern double XPLMGetDatad(XPLMDataRef inDataRef);
extern int XPLMGetDatai(XPLMDataRef inDataRef);
extern float XPLMGetDataf(XPLMDataRef inDataRef);
extern void XPLMDebugString(const char *inString);
extern void XPLMRegisterFlightLoopCallback(XPLMFlightLoop_f inFlightLoop, float inInterval, void *inRefcon);
extern void XPLMUnregisterFlightLoopCallback(XPLMFlightLoop_f inFlightLoop, void *inRefcon);
extern void XPLMGetSystemPath(char *outSystemPath);
extern void XPLMEnableFeature(const char *inFeature, int inEnable);

#define XPLM_PLUGIN_XPLANE 0
#define XPLM_MSG_PLANE_LOADED 102
#define XPLM_MSG_LIVERY_LOADED 108
#define XPLM_MSG_FMOD_BANK_LOADED 112
#define XPLM_RADIO_BANK 1

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
#ifndef ALONE_S
#define ALONE_S 0.5 /* at X-Plane's start: how long a streamer may take to answer that it is there */
#endif
#define NOTE_EVERY_S 1.0f /* in flight: whether the streamer has a notice */
/* X-Plane's texture quality, 0 to 5 (Maximum): a private dataref of X-Plane's, the setting's own,
   not there yet when the plugin starts (X-Plane 12.4.3, 2026-11-13) */
#define QUALITY_REF "sim/private/controls/reno/tex_res"
#define QUALITY_LOOK_S 1.0 /* while it is not there: looked for again this often */

/* The loading window and the notice (live_panel.m, live_panel_win.c, live_panel_x11.c). */
void live_panel_show(const char *what, const char *progress, double fraction);
void live_panel_hide(void);
void live_note_show(const char *text, int french); /* the notice, with its cross */
void live_note_hide(void);
int live_note_closed(void); /* 1: its cross clicked since the last call */
#ifdef __APPLE__
static void live_panel_tick(void) {}
static void live_panels_close(void) {
    live_panel_hide();
    live_note_hide();
}
#else
void live_panel_tick(void);   /* while X-Plane waits, every ASK_EVERY_S at most */
void live_panels_close(void); /* the plugin stops: both gone, and what drew them */
#endif

/* The socket to the streamer: Winsock's on Windows, where a UDP port nobody listens on comes back
   as WSAECONNRESET at the next receive (ECONNREFUSED elsewhere). It never blocks: the waits are
   timed by wait_for. */
#ifdef _WIN32
typedef SOCKET live_socket;
#define NO_SOCKET INVALID_SOCKET
#define close_socket closesocket
static int refused(void) {
    int e = WSAGetLastError();
    return e == WSAECONNRESET || e == WSAECONNREFUSED;
}
static int winsock = 0; /* WSAStartup done */
#else
typedef int live_socket;
#define NO_SOCKET (-1)
#define close_socket close
static int refused(void) { return errno == ECONNREFUSED; }
#endif

static live_socket sock = NO_SOCKET;
static int holding = 0; /* hold() runs: on Windows X-Plane's window may be handed a message
                           meanwhile (live_panel_tick), which may come back here */
static int flights = 0; /* flights whose aircraft has loaded */
static XPLMDataRef lat_ref, lon_ref;
static XPLMDataRef day_ref; /* the flight's date: its day of the year, from 0 */
static XPLMDataRef utc_ref; /* ... and its UTC time, in seconds */
static int noted = 0, closed_note = 0; /* the notice shown (its id), and the last one closed */
static int french = 0; /* the Live window's language, which each answer says: its words in X-Plane */
static char note[160]; /* its words */
static XPLMDataRef quality_ref; /* X-Plane's texture quality, once found (texture_quality) */
static double quality_looked = -1.0; /* when it was last looked for */
static int quality = -1; /* the quality last told to the streamer; -1: none read yet */
static int quality_missing_said = 0;

static double now(void) {
#ifdef _WIN32
    static LARGE_INTEGER hz;
    LARGE_INTEGER t;
    if (hz.QuadPart == 0) QueryPerformanceFrequency(&hz);
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart / (double)hz.QuadPart;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
#endif
}

static void pause_for(double s) {
#ifdef _WIN32
    if (s > 0) Sleep((DWORD)(s * 1000.0 + 0.5));
#else
    struct timespec ts;
    if (s <= 0) return;
    ts.tv_sec = (time_t)s;
    ts.tv_nsec = (long)((s - (double)ts.tv_sec) * 1e9);
    nanosleep(&ts, NULL);
#endif
}

static int never_blocks(live_socket s) {
#ifdef _WIN32
    u_long on = 1;
    return ioctlsocket(s, FIONBIO, &on) == 0;
#else
    int flags = fcntl(s, F_GETFL, 0);
    return flags >= 0 && fcntl(s, F_SETFL, flags | O_NONBLOCK) == 0;
#endif
}

/* Up to `seconds` for something to read, or an error to collect: > 0 then, 0 if none came. poll,
   not select, off Windows: X-Plane may hold more files open than select's sets have room for. */
static int wait_for(live_socket s, double seconds) {
#ifdef _WIN32
    fd_set readable, failed;
    struct timeval tv;
    FD_ZERO(&readable);
    FD_ZERO(&failed);
    FD_SET(s, &readable);
    FD_SET(s, &failed);
    tv.tv_sec = (long)seconds;
    tv.tv_usec = (long)((seconds - (double)tv.tv_sec) * 1e6);
    return select(0, &readable, NULL, &failed, &tv);
#else
    struct pollfd p;
    int ms = (int)(seconds * 1000.0 + 0.999);
    p.fd = s;
    p.events = POLLIN;
    p.revents = 0;
    return poll(&p, 1, ms < 1 ? 1 : ms);
#endif
}

/* What is there to read now, without waiting: its length, or < 0 (nothing, or an error). */
static long recv_now(live_socket s, char *buf, size_t size) {
#ifdef _WIN32
    return recv(s, buf, (int)size, 0);
#else
    return (long)recv(s, buf, size, 0);
#endif
}

static void send_line(live_socket s, const char *line) {
#ifdef _WIN32
    send(s, line, (int)strlen(line), 0);
#else
    send(s, line, strlen(line), 0);
#endif
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

static live_socket open_socket(void) {
    struct sockaddr_in addr;
    live_socket s = socket(AF_INET, SOCK_DGRAM, 0);
    if (s == NO_SOCKET) return NO_SOCKET;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons(LIVE_PORT);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    /* Connected: a port nobody listens on shows as refused at once. */
    if (connect(s, (struct sockaddr *)&addr, sizeof addr) != 0 || !never_blocks(s)) {
        close_socket(s);
        return NO_SOCKET;
    }
    return s;
}

static void drain(void) {
    char buf[256];
    while (recv_now(sock, buf, sizeof buf) >= 0) {
    }
}

/* What the streamer says while X-Plane waits: "LIVE1 WAIT n=12 b=4 p=450 s=23 mbs=15.2 lang=en at=LFBO runway
   14L" is 12 textures left, 4 of them with all their photos, being built (b=, the rest waiting for
   theirs: all of them, "every photo in" instead of the rate), 45.0 % of the work done, 23 s since
   the start was known, downloading at 15.2 MB/s, the Live window's language (the words shown take
   it), and where (the rest of the line, in that language). "night=7": 7 of the textures left are
   night ones (a start at night, after 2026-11-11; an older streamer says none). */
struct state {
    int left, building, permille, secs;
    double mbs;
    char where[128];
    int night;
};

static void parse_wait(char *p, struct state *st) {
    while (*p) {
        while (*p == ' ') p++;
        if (strncmp(p, "at=", 3) == 0) {
            if (p[3]) snprintf(st->where, sizeof st->where, "%s", p + 3);
            return;
        }
        if (sscanf(p, "n=%d", &st->left) != 1 && sscanf(p, "b=%d", &st->building) != 1 &&
            sscanf(p, "night=%d", &st->night) != 1 &&
            sscanf(p, "p=%d", &st->permille) != 1 &&
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
    double until = now() + wait_s;
    drain();
    send_line(sock, line);
    for (;;) {
        double rest = until - now();
        long n;
        if (rest <= 0) return -1;
        if (wait_for(sock, rest) <= 0) continue; /* none yet: rest is over, or a signal came */
        n = recv_now(sock, buf, sizeof buf - 1);
        if (n < 0) return refused() ? -2 : -1;
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
    if (sock == NO_SOCKET || !position(&lat, &lon)) return;
    snprintf(line, sizeof line, "LIVE1 LOAD %.6f %.6f", lat, lon);
    drain();
    send_line(sock, line);
}

/* X-Plane's texture quality now, or -1 while its dataref is not there (looked for again every
   QUALITY_LOOK_S: an X-Plane without it leaves the streamer with X-Plane's preferences). */
static int texture_quality(void) {
    if (!quality_ref) {
        double t = now();
        if (quality_looked >= 0.0 && t - quality_looked < QUALITY_LOOK_S) return -1;
        quality_looked = t;
        quality_ref = XPLMFindDataRef(QUALITY_REF);
        if (!quality_ref) return -1;
    }
    return XPLMGetDatai(quality_ref);
}

static void tell_quality(void) {
    char line[48];
    if (sock == NO_SOCKET || quality < 0) return;
    snprintf(line, sizeof line, "LIVE1 QUALITY %d", quality);
    send_line(sock, line);
}

/* One line: the textures left, then the download rate, or once every photo they wait for is in
   (all of them being built: b= reaches n=), that instead of a rate of 0.0, which read as stuck
   while the count went down (the user's Shadow, 2026-10-11). `sharper`: X-Plane held for its
   texture quality raised in flight (sharpen), not at a start: its own first line, and no place. */
static void show(const struct state *st, int sharper) {
    char what[200], progress[120];
    int photos_in = st->left > 0 && st->building >= st->left;
    int nights_only = st->night > 0 && st->night >= st->left; /* the photos built: the night left */
    if (sharper && french)
        snprintf(what, sizeof what, "Qualité relevée : textures plus nettes autour de l'avion");
    else if (sharper)
        snprintf(what, sizeof what, "Quality raised: sharper textures around the aircraft");
    else if (french)
        snprintf(what, sizeof what, "Chargement de l'ortho autour de %s", st->where);
    else
        snprintf(what, sizeof what, "Loading the ortho around %s", st->where);
    if (french && nights_only) {
        snprintf(progress, sizeof progress, "%d textures de nuit à construire  -  %d s", st->left,
                 st->secs);
    } else if (nights_only) {
        snprintf(progress, sizeof progress, "%d night textures to build  -  %d s", st->left,
                 st->secs);
    } else if (french) {
        char rate[48], *dot;
        snprintf(rate, sizeof rate, "%.1f Mo/s", st->mbs);
        if ((dot = strchr(rate, '.')) != NULL) *dot = ','; /* 15,2 Mo/s */
        snprintf(progress, sizeof progress, "%d textures à construire  -  %s  -  %d s", st->left,
                 photos_in ? "photos reçues, construction en cours" : rate, st->secs);
    } else {
        char rate[48];
        snprintf(rate, sizeof rate, "%.1f MB/s", st->mbs);
        snprintf(progress, sizeof progress, "%d textures to build  -  %s  -  %d s", st->left,
                 photos_in ? "every photo in, building" : rate, st->secs);
    }
    live_panel_show(what, progress, st->permille / 1000.0);
}

/* X-Plane held, asking `line` every ASK_EVERY_S, until the streamer says go, or stops, or never
   answers: at a start (`at`, the message it holds at), or for the texture quality raised in
   flight to `sharper` (`at` NULL). */
static void hold_until_built(const char *line, const char *at, int sharper, double lat, double lon) {
    struct state st = {-1, 0, 0, 0, 0.0, "", 0};
    double start, answered = 0, t;
    int told = 0, r;
    snprintf(st.where, sizeof st.where, "%.3f %.3f", lat, lon);
    start = now();
    holding = 1;
    for (;;) {
        double asked = now();
        live_panel_tick();
        if (sock == NO_SOCKET) break; /* the plugin disabled meanwhile (X-Plane quitting) */
        r = ask(line, ASK_EVERY_S, &st);
        t = now();
        if (r == 1) {
            if (told) {
                if (at)
                    say("the ortho is built after %.0f s: X-Plane goes on", t - start);
                else
                    say("the sharper textures are built after %.0f s: X-Plane goes on", t - start);
                live_panel_hide();
            }
            holding = 0;
            return;
        }
        if (r == -2) { /* nothing listens: no streamer, or it stopped */
            if (answered > 0) say("the streamer stopped: X-Plane goes on");
            break;
        }
        if (r == 0) {
            answered = t;
            if (!told && at)
                say("X-Plane waits at %s until the ortho around %s is built (%d textures to go)", at, st.where, st.left);
            else if (!told)
                say("X-Plane's texture quality rose to %d: X-Plane waits until the sharper textures around %s are "
                    "built (%d textures to go)", sharper, st.where, st.left);
            told = 1;
            show(&st, !at);
            if (t - asked < ASK_EVERY_S) pause_for(ASK_EVERY_S - (t - asked));
        }
        if (answered == 0 && t - start > NO_STREAMER_S) break;
        if (answered > 0 && t - answered > SILENT_S) {
            say("the streamer stopped answering: X-Plane goes on");
            break;
        }
    }
    live_panel_hide();
    holding = 0;
}

/* At a start: X-Plane's texture quality told first, which the streamer builds the start for. */
static void hold(const char *at) {
    char line[96];
    double lat, lon;
    int q;
    if (sock == NO_SOCKET || !position(&lat, &lon)) return;
    if ((q = texture_quality()) >= 0) quality = q;
    tell_quality();
    /* the date too: the streamer gives the photos its season (a start's, never in flight), and
       the UTC time, whether it is night there (the local time X-Plane saves is its zone's) */
    snprintf(line, sizeof line, "LIVE1 HOLD %.6f %.6f aircraft day=%d utc=%.0f", lat, lon,
             day_ref ? XPLMGetDatai(day_ref) : -1, utc_ref ? XPLMGetDataf(utc_ref) : -1.0);
    hold_until_built(line, at, 0, lat, lon);
}

/* In flight, X-Plane's texture quality raised to `q`: X-Plane held where it is, before it reads the
   sharper levels round the aircraft (3 to 6 s after "Resume flight"). */
static void sharpen(int q) {
    char line[96];
    double lat, lon;
    if (sock == NO_SOCKET || !position(&lat, &lon)) return;
    snprintf(line, sizeof line, "LIVE1 SHARPEN %.6f %.6f %d", lat, lon, q);
    hold_until_built(line, NULL, q, lat, lon);
}

#define FRAME_GAP_S 5.0 /* longer between two frames: X-Plane loaded or was held, no stutter */
static double frame_last = 0.0, frame_max = 0.0; /* the longest frame of the second */
static int frames = 0;

/* Every frame (it returns -1): the time since the one before, the longest of each second kept for
   the ask, which tells the streamer: X-Plane's stutters, next to what Live was doing (a user saw
   some while Live built, 2026-09-28). And X-Plane's texture quality: a new one told, and X-Plane
   held right there when it rose in a flight (sharpen). */
static float every_frame(float since_call, float since_loop, int counter, void *refcon) {
    double t = now(), gap = frame_last > 0.0 ? t - frame_last : 0.0;
    int q;
    (void)since_call;
    (void)since_loop;
    (void)counter;
    (void)refcon;
    frame_last = t;
    if (gap > 0.0 && gap < FRAME_GAP_S) {
        if (gap > frame_max) frame_max = gap;
        frames++;
    }
    if (!holding && sock != NO_SOCKET && (q = texture_quality()) >= 0 && q != quality) {
        int rose = quality >= 0 && q > quality && flights > 0; /* the first reading: as it is */
        quality = q;
        tell_quality();
        if (rose) {
            sharpen(q);
            frame_last = now(); /* the hold is no frame of X-Plane's */
        }
    }
    return -1.0f;
}

/* Every second of a flight: whether the streamer has a notice ("LIVE1 NOTE id text"), and whether
   its cross was clicked. The answers to the last ask are read without waiting. The ask says the
   second's frames: "LIVE1 ASK max=123 n=45 q=5", the longest in ms, how many, and X-Plane's texture
   quality (none while it cannot be read). */
static float every_second(float since_call, float since_loop, int counter, void *refcon) {
    char buf[256];
    long n;
    int id, at;
    (void)since_call;
    (void)since_loop;
    (void)counter;
    (void)refcon;
    if (sock == NO_SOCKET || flights == 0) return NOTE_EVERY_S;
    if (live_note_closed() && noted) closed_note = noted; /* its cross */
    while ((n = recv_now(sock, buf, sizeof buf - 1)) >= 0) {
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
    if (!quality_ref && quality_looked >= 0.0 && !quality_missing_said) {
        say("X-Plane's texture quality cannot be read (" QUALITY_REF "): Live goes by X-Plane's "
            "preferences as it started");
        quality_missing_said = 1;
    }
    if (quality >= 0) /* X-Plane's texture quality too: a lower one, or a streamer started since */
        snprintf(buf, sizeof buf, "LIVE1 ASK max=%d n=%d q=%d", (int)(frame_max * 1000.0 + 0.5), frames,
                 quality);
    else
        snprintf(buf, sizeof buf, "LIVE1 ASK max=%d n=%d", (int)(frame_max * 1000.0 + 0.5), frames);
    frame_max = 0.0;
    frames = 0;
    send_line(sock, buf);
    return NOTE_EVERY_S;
}

/* Whether a streamer listens on Live's port: only a refusal says no (nothing bound there). */
static int streamer_listens(void) {
    char buf[64];
    long n = 0;
    int no;
    live_socket s = open_socket();
    if (s == NO_SOCKET) return 1; /* cannot tell: everything stays as it is */
    send_line(s, "LIVE1 HELLO");
    if (wait_for(s, ALONE_S) > 0) n = recv_now(s, buf, sizeof buf);
    no = n < 0 && refused();
    close_socket(s);
    return !no;
}

/* Live's link in Custom Scenery taken out, never a folder: a symbolic link, or on Windows a
   junction (or a symbolic link where Developer Mode allows it), removed as a link, what it leads
   to untouched. 1 if it was there and went. */
#ifdef _WIN32
#define SEP "\\"
static int remove_link(const char *path) {
    wchar_t w[1300];
    WIN32_FIND_DATAW found;
    HANDLE h;
    if (!MultiByteToWideChar(CP_UTF8, 0, path, -1, w, (int)(sizeof w / sizeof *w))) return 0;
    h = FindFirstFileW(w, &found); /* the entry itself, not where it leads */
    if (h == INVALID_HANDLE_VALUE) return 0;
    FindClose(h);
    if (!(found.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
        !(found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY))
        return 0;
    if (found.dwReserved0 != IO_REPARSE_TAG_MOUNT_POINT && found.dwReserved0 != IO_REPARSE_TAG_SYMLINK)
        return 0; /* another kind (a cloud file...): not Live's */
    return RemoveDirectoryW(w) != 0;
}
#else
#define SEP "/"
static int remove_link(const char *path) {
    struct stat st;
    return lstat(path, &st) == 0 && S_ISLNK(st.st_mode) && unlink(path) == 0;
}
#endif

/* Live's scenery in Custom Scenery while no streamer is there: left behind (Live stopped without
   taking it out, crashed, the computer restarted). X-Plane lists its scenery after it starts its
   plugins (its Log.txt), so taken out now it is never read: Live makes the files its DSFs name only
   round the aircraft while it runs, and elsewhere X-Plane would find none (2026-09-30). Only links
   Live made, never a folder; Live puts them back at its next start. */
static void take_out_if_alone(void) {
    static const char *links[] = {"z_osxp_live", "y_osxp_live_overlays"};
    char root[1024], path[1300];
    size_t i;
    if (streamer_listens()) return;
    XPLMEnableFeature("XPLM_USE_NATIVE_PATHS", 1); /* on the Mac, "/Users/..." */
    root[0] = 0;
    XPLMGetSystemPath(root); /* with its separator at the end, in UTF-8 */
    if (!root[0]) return;
    for (i = 0; i < sizeof links / sizeof *links; i++) {
        snprintf(path, sizeof path, "%sCustom Scenery" SEP "%s", root, links[i]);
        if (remove_link(path))
            say("Live is not running: its scenery %s is out of X-Plane until Live starts again", links[i]);
    }
}

PLUGIN_API int XPluginStart(char *outName, char *outSig, char *outDesc) {
    strcpy(outName, "OrthoStudio XP Live");
    strcpy(outSig, "orthostudio.live");
    strcpy(outDesc, "Makes X-Plane wait until OrthoStudio XP Live has built the ortho it reads: while a flight loads, "
                    "and when its texture quality rises in flight.");
    lat_ref = XPLMFindDataRef("sim/flightmodel/position/latitude");
    lon_ref = XPLMFindDataRef("sim/flightmodel/position/longitude");
    day_ref = XPLMFindDataRef("sim/time/local_date_days");
    utc_ref = XPLMFindDataRef("sim/time/zulu_time_sec");
#ifdef _WIN32
    {
        WSADATA wsa;
        winsock = WSAStartup(MAKEWORD(2, 2), &wsa) == 0;
    }
    DisableProcessWindowsGhosting(); /* paused by Live, X-Plane is not greyed nor offered to close */
#endif
    take_out_if_alone();
    return 1;
}

PLUGIN_API void XPluginStop(void) {
    live_panels_close();
#ifdef _WIN32
    if (winsock) WSACleanup();
    winsock = 0;
#endif
}

PLUGIN_API int XPluginEnable(void) {
    sock = open_socket();
    if (sock == NO_SOCKET) say("no UDP socket: X-Plane will not wait for the ortho");
    XPLMRegisterFlightLoopCallback(every_second, NOTE_EVERY_S, NULL);
    XPLMRegisterFlightLoopCallback(every_frame, -1.0f, NULL);
    return 1;
}

PLUGIN_API void XPluginDisable(void) {
    XPLMUnregisterFlightLoopCallback(every_second, NULL);
    XPLMUnregisterFlightLoopCallback(every_frame, NULL);
    live_note_hide();
    if (sock != NO_SOCKET) close_socket(sock);
    sock = NO_SOCKET;
}

PLUGIN_API void XPluginReceiveMessage(XPLMPluginID inFrom, int inMsg, void *inParam) {
    intptr_t param = (intptr_t)inParam;
    if (inFrom != XPLM_PLUGIN_XPLANE) return;
    say("message %d (%ld)", inMsg, (long)param);
    if (holding) return; /* sent from within a hold (see `holding`): it holds already */
    if (inMsg == XPLM_MSG_PLANE_LOADED && param == 0) {
        flights++;
        tell_start();
    } else if (inMsg == XPLM_MSG_FMOD_BANK_LOADED && param == XPLM_RADIO_BANK)
        hold("the radio sounds");
    else if (inMsg == XPLM_MSG_LIVERY_LOADED && param == 0)
        hold("the livery");
}
