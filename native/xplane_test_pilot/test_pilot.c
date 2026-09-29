/* OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
 * Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE. */

/*
 * OrthoStudio XP Live's test pilot: a flight X-Plane flies the same way every time, with nobody at
 * the controls, for the replayable test flights (tools/live/flights, docs/specs/live/flights.md).
 *
 * X-Plane stays up from one test to the next: opening and closing it took minutes each time (the
 * user: "Il faut que l'ouverture et chargement et fermeture soient rapides"). Every second the
 * plugin looks in Output/osxp_test_pilot/ of X-Plane's folder for what the runner asks:
 * - scenario.txt, a new one (its time changed): a flight, flown as below; then it is renamed
 *   scenario.done, and events.txt says "done";
 * - quit: X-Plane quits. X-Plane asks "Quit the simulator?" at this quit command, and waits for a
 *   click (11 minutes, 2026-09-29); plugins' flight loops do not run while it asks, but a second
 *   quit command over UDP is its answer: X-Plane then shuts down at once, cleanly. The runner sends
 *   it (tools/live/flights/fly.py).
 * Without them it does nothing. A flight:
 * - it places the aircraft where the scenario starts, in flight at its height, heading and speed,
 *   its engines running (XPLMPlaceUserAtLocation, as X-Plane's own starts in the air), and sets
 *   the weather and the time it says;
 * - once X-Plane flies there (its clock running, the aircraft at the start: Live's plugin may hold
 *   X-Plane first while the ortho is built), it flies the scenario's path itself, frame by frame
 *   from X-Plane's own clock: legs of a duration, a turn rate and a climb rate at one ground speed.
 *   The position is converted by X-Plane in its own thread and frame (XPLMWorldToLocal), so X-Plane
 *   moving its local origin as it loads scenery moves nothing, and its clock runs on: the drones
 *   over UDP of 2026-09-29 threw the aircraft a degree away (local coordinates written in an old
 *   frame) or stopped X-Plane's time (VEHX). The velocity is set with the position;
 * - it points the camera where the scenario says (XPLMControlCamera), from the aircraft's own
 *   position each frame: the same views every run;
 * - it takes screenshots at the scenario's times, and writes every frame (time, duration,
 *   position, camera) to Output/osxp_test_pilot/frames.csv, and what it does to events.txt;
 * - at the end it pauses X-Plane, or quits it.
 *
 * The scenario, one statement a line ('#' starts a comment):
 *   start LAT LON FEET HEADING KNOTS     where and how the flight starts
 *   leg SECONDS DEG_PER_S FEET_PER_MIN   the path, from the start, one leg after the other
 *   weather clear                        no cloud, no wind, no turbulence, the weather static
 *   zulu HOURS [DAY]                     the time of day, UTC, and the day of the year (0: 1 January)
 *   camera T0 T1 cockpit                 the 3D cockpit between T0 and T1 seconds of path
 *   camera T0 T1 chase METRES UP BEARING a camera METRES from the aircraft, UP above it, at
 *                                        BEARING degrees from its heading, looking at it
 *   camera-record                        the user's camera recorded, 20 times a second of path, into
 *                                        camera.csv: the plugin leaves the view to the user
 *   camera-track FILE                    a camera recorded so replayed, as the user moved it, and
 *                                        again from its start when shorter than the path (a loop:
 *                                        the user's wish, 2026-09-29)
 *   screenshot T                         a screenshot at T seconds of path
 *   end quit|stay                        what then (stay: X-Plane stays up, paused, for the next)
 *   aircraft PATH                        for the runner, which starts X-Plane with it (--load_acf)
 *
 * X-Plane's scenery loads are marked in Log.txt, where the plugin's lines fall in order among
 * X-Plane's own ("scenery load at LAT LON, path T s", the path's time -1 before it starts): the
 * DSFs X-Plane logs after one came with it. Its end is marked too ("scenery loaded in S s"): X-Plane
 * keeps its own lines until a plugin writes one (2026-09-29: a row's DSFs still unwritten 5 min
 * after their load), so the end's mark also puts the load's DSFs in the file.
 *
 * Declares the few XPLM functions it uses itself, as live_gate.c: no SDK needed to build it.
 */

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define PLUGIN_API __attribute__((visibility("default")))

typedef void *XPLMDataRef;
typedef void *XPLMCommandRef;
typedef int XPLMPluginID;
typedef float (*XPLMFlightLoop_f)(float inElapsedSinceLastCall, float inElapsedTimeSinceLastFlightLoop,
                                  int inCounter, void *inRefcon);
typedef struct {
    float x, y, z, pitch, heading, roll, zoom;
} XPLMCameraPosition_t;
typedef int (*XPLMCameraControl_f)(XPLMCameraPosition_t *outCameraPosition, int inIsLosingControl, void *inRefcon);
extern XPLMDataRef XPLMFindDataRef(const char *inDataRefName);
extern double XPLMGetDatad(XPLMDataRef inDataRef);
extern float XPLMGetDataf(XPLMDataRef inDataRef);
extern int XPLMGetDatai(XPLMDataRef inDataRef);
extern void XPLMSetDatad(XPLMDataRef inDataRef, double inValue);
extern void XPLMSetDataf(XPLMDataRef inDataRef, float inValue);
extern void XPLMSetDatai(XPLMDataRef inDataRef, int inValue);
extern void XPLMSetDatavf(XPLMDataRef inDataRef, float *inValues, int inoffset, int inCount);
extern void XPLMSetDatavi(XPLMDataRef inDataRef, int *inValues, int inoffset, int inCount);
extern XPLMCommandRef XPLMFindCommand(const char *inName);
extern void XPLMCommandOnce(XPLMCommandRef inCommand);
extern void XPLMDebugString(const char *inString);
extern void XPLMGetSystemPath(char *outSystemPath);
extern void XPLMRegisterFlightLoopCallback(XPLMFlightLoop_f inFlightLoop, float inInterval, void *inRefcon);
extern void XPLMUnregisterFlightLoopCallback(XPLMFlightLoop_f inFlightLoop, void *inRefcon);
extern void XPLMWorldToLocal(double inLatitude, double inLongitude, double inAltitude, double *outX, double *outY,
                             double *outZ);
extern void XPLMPlaceUserAtLocation(double latitudeDegrees, double longitudeDegrees, float elevationMetersMSL,
                                    float headingDegreesTrue, float speedMetersPerSecond);
extern void XPLMControlCamera(int inHowLong, XPLMCameraControl_f inControlFunc, void *inRefcon);
extern void XPLMDontControlCamera(void);
extern void XPLMEnableFeature(const char *inFeature, int inEnable);

#define XPLM_CONTROL_CAMERA_FOREVER 2
#define FT 0.3048
#define KT 0.514444
#define M_DEG 111195.0
#define STEP 0.5                /* the path's samples, seconds */
#define MAX_LEGS 64
#define MAX_CAMERAS 64
#define MAX_SHOTS 256
#define READY_S 3.0             /* X-Plane flying at the start this long: the path begins */
#define START_KM 20.0
#define TRACK_EVERY 0.05        /* the user's camera recorded 20 times a second of path */
#define MAX_TRACK 200000        /* poses: more than 2 h of path */

enum { WAITING, PLACED, FLYING, DONE };

struct leg { double seconds, turn, climb; };
/* The user's camera at a time of the path. Outside (ext): a ahead, b right, c up of the aircraft,
   metres; h its heading from the aircraft's, p its pitch, r its roll, degrees. Inside: the pilot's
   head, a b c its x y z (metres, the aircraft's axes), h p r its psi the phi. fov: X-Plane's
   field of view, degrees. */
struct pose { double t, a, b, c, h, p, r, fov; int ext; };
struct camera { double t0, t1, dist, up, bearing; int chase; };

static char dir[1024], box[1100]; /* X-Plane's folder, and the runner's: Output/osxp_test_pilot/ */
static long scenario_time = 0; /* the scenario flown last: its file's time */
static double polled = -10.0; /* the wall clock's last look at the runner's files */
static int active = 0, state = WAITING, end_quit = 0, weather_clear = 0, placed_frames = 0;
static double start_lat, start_lon, start_ft, start_hdg, start_kt, zulu_h = -1.0, day = -1.0;
static struct leg legs[MAX_LEGS];
static int n_legs = 0;
static struct camera cameras[MAX_CAMERAS];
static int n_cameras = 0, camera_on = 0, cockpit_set = -1; /* the cockpit camera last set */
static int camera_record = 0, inside_set = 0; /* recording the user's camera; the cockpit set */
static int replaying = 0; /* a recorded camera replayed */
static FILE *camfile = NULL;
static double cam_last = -1.0;
static struct pose *track = NULL; /* a camera recorded, replayed */
static int n_track = 0;
static char track_file[600];
static double shots[MAX_SHOTS];
static int n_shots = 0, next_shot = 0;
static double *path = NULL; /* lat, lon, metres, heading every STEP s */
static int n_path = 0;
static double run0 = 0.0, ready_since = -1.0, path_t = 0.0;
static FILE *frames = NULL, *events = NULL;
static double frame_flushed = 0.0;
static XPLMDataRef r_lat, r_lon, r_ele, r_run, r_paused, r_x, r_y, r_z, r_vx, r_vy, r_vz, r_psi, r_theta, r_phi,
    r_q, r_override, r_view_x, r_view_y, r_view_z, r_loading, r_ext, r_vhdg, r_vpitch, r_vroll, r_fov, r_head[6];
static int loading = 0; /* X-Plane loading scenery, as the last frame saw it */
static double load_began = 0.0; /* X-Plane's clock when that load began */

static void say(const char *fmt, ...) {
    char text[400], line[440];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    snprintf(line, sizeof line, "OrthoStudio XP Test Pilot: %s\n", text);
    XPLMDebugString(line);
    if (events) {
        fprintf(events, "%.3f %s\n", XPLMGetDataf(r_run), text);
        fflush(events);
    }
}

static int read_scenario(const char *file) {
    char line[512], word[32];
    FILE *f = fopen(file, "r");
    if (!f) return 0;
    active = 0, end_quit = 0, weather_clear = 0, n_legs = 0, n_cameras = 0, n_shots = 0, next_shot = 0;
    zulu_h = -1.0, day = -1.0, camera_record = 0, track_file[0] = 0;
    while (fgets(line, sizeof line, f)) {
        char *hash = strchr(line, '#');
        double a, b, c, d, e;
        if (hash) *hash = 0;
        if (sscanf(line, "%31s", word) != 1) continue;
        if (strcmp(word, "start") == 0 && sscanf(line, "start %lf %lf %lf %lf %lf", &a, &b, &c, &d, &e) == 5) {
            start_lat = a, start_lon = b, start_ft = c, start_hdg = d, start_kt = e;
            active = 1;
        } else if (strcmp(word, "leg") == 0 && n_legs < MAX_LEGS && sscanf(line, "leg %lf %lf %lf", &a, &b, &c) == 3) {
            legs[n_legs++] = (struct leg){a, b, c};
        } else if (strcmp(word, "weather") == 0 && strstr(line, "clear")) {
            weather_clear = 1;
        } else if (strcmp(word, "zulu") == 0 && sscanf(line, "zulu %lf", &a) == 1) {
            zulu_h = a;
            if (sscanf(line, "zulu %lf %lf", &a, &b) == 2) day = b;
        } else if (strcmp(word, "camera") == 0 && n_cameras < MAX_CAMERAS) {
            struct camera cam = {0};
            char mode[32];
            if (sscanf(line, "camera %lf %lf %31s %lf %lf %lf", &cam.t0, &cam.t1, mode, &cam.dist, &cam.up, &cam.bearing) >= 3) {
                cam.chase = strcmp(mode, "chase") == 0;
                cameras[n_cameras++] = cam;
            }
        } else if (strcmp(word, "camera-record") == 0) {
            camera_record = 1;
        } else if (strcmp(word, "camera-track") == 0) { /* the rest of the line: a path, spaces and all */
            char *from = strstr(line, "camera-track") + strlen("camera-track"), *end;
            while (*from == ' ' || *from == '\t') from++;
            end = from + strlen(from);
            while (end > from && (end[-1] == '\n' || end[-1] == '\r' || end[-1] == ' ' || end[-1] == '\t')) *--end = 0;
            snprintf(track_file, sizeof track_file, "%s", from);
        } else if (strcmp(word, "screenshot") == 0 && n_shots < MAX_SHOTS && sscanf(line, "screenshot %lf", &a) == 1) {
            shots[n_shots++] = a;
        } else if (strcmp(word, "end") == 0) {
            end_quit = strstr(line, "quit") != NULL;
        }
    }
    fclose(f);
    return active && n_legs > 0;
}

/* The path, sampled every STEP s from the start: (lat, lon, metres, heading). */
static void make_path(void) {
    double lat = start_lat, lon = start_lon, alt = start_ft * FT, hdg = start_hdg, v = start_kt * KT;
    int total = 1, i, k, n;
    for (i = 0; i < n_legs; i++) total += (int)lround(legs[i].seconds / STEP);
    path = malloc(sizeof(double) * 4 * total);
    n_path = 0;
    path[0] = lat, path[1] = lon, path[2] = alt, path[3] = hdg;
    n_path = 1;
    for (i = 0; i < n_legs; i++) {
        n = (int)lround(legs[i].seconds / STEP);
        for (k = 0; k < n; k++) {
            double h;
            hdg = fmod(hdg + legs[i].turn * STEP + 360.0, 360.0);
            h = hdg * M_PI / 180.0;
            lat += v * STEP * cos(h) / M_DEG;
            lon += v * STEP * sin(h) / (M_DEG * cos(lat * M_PI / 180.0));
            alt += legs[i].climb * FT / 60.0 * STEP;
            path[4 * n_path] = lat, path[4 * n_path + 1] = lon, path[4 * n_path + 2] = alt, path[4 * n_path + 3] = hdg;
            n_path++;
        }
    }
}

static void target(double t, double *lat, double *lon, double *alt, double *hdg) {
    int i = (int)(t / STEP);
    double f, dh;
    if (i < 0) i = 0;
    if (i > n_path - 2) i = n_path - 2;
    f = t / STEP - i;
    if (f < 0) f = 0;
    if (f > 1) f = 1;
    *lat = path[4 * i] + (path[4 * i + 4] - path[4 * i]) * f;
    *lon = path[4 * i + 1] + (path[4 * i + 5] - path[4 * i + 1]) * f;
    *alt = path[4 * i + 2] + (path[4 * i + 6] - path[4 * i + 2]) * f;
    dh = fmod(path[4 * i + 7] - path[4 * i + 3] + 540.0, 360.0) - 180.0;
    *hdg = fmod(path[4 * i + 3] + dh * f + 360.0, 360.0);
}

static double km(double lat1, double lon1, double lat2, double lon2) {
    double dlon = fmod(lon2 - lon1 + 540.0, 360.0) - 180.0;
    return hypot((lat2 - lat1) * M_DEG, dlon * M_DEG * cos(lat1 * M_PI / 180.0)) / 1000.0;
}

static void quaternion(double psi, float q[4]) { /* level flight: heading only */
    double p = psi * M_PI / 360.0;
    q[0] = (float)cos(p), q[1] = 0.0f, q[2] = 0.0f, q[3] = (float)sin(p);
}

static void set_weather(void) {
    float zeros[13] = {0};
    XPLMSetDatai(XPLMFindDataRef("sim/weather/region/change_mode"), 3); /* static */
    XPLMSetDatavf(XPLMFindDataRef("sim/weather/region/cloud_coverage_percent"), zeros, 0, 3);
    XPLMSetDatavf(XPLMFindDataRef("sim/weather/region/wind_speed_msc"), zeros, 0, 13);
    XPLMSetDatavf(XPLMFindDataRef("sim/weather/region/turbulence"), zeros, 0, 13);
    XPLMSetDatai(XPLMFindDataRef("sim/weather/region/update_immediately"), 1);
}

/* The aircraft at the path's point for t, at the path's speed, from X-Plane's own frame. */
static void place(double t) {
    double lat, lon, alt, hdg, x, y, z, lat2, lon2, alt2, hdg2, x2, y2, z2;
    float q[4];
    int one = 1;
    target(t, &lat, &lon, &alt, &hdg);
    target(t + 1.0, &lat2, &lon2, &alt2, &hdg2);
    XPLMWorldToLocal(lat, lon, alt, &x, &y, &z);
    XPLMWorldToLocal(lat2, lon2, alt2, &x2, &y2, &z2);
    XPLMSetDatavi(r_override, &one, 0, 1);
    XPLMSetDatad(r_x, x);
    XPLMSetDatad(r_y, y);
    XPLMSetDatad(r_z, z);
    XPLMSetDataf(r_vx, (float)(x2 - x)); /* a second ahead: the velocity, m/s */
    XPLMSetDataf(r_vy, (float)(y2 - y));
    XPLMSetDataf(r_vz, (float)(z2 - z));
    XPLMSetDataf(r_psi, (float)hdg);
    XPLMSetDataf(r_theta, 0.0f);
    XPLMSetDataf(r_phi, 0.0f);
    quaternion(hdg, q);
    XPLMSetDatavf(r_q, q, 0, 4);
}

/* A camera recorded (camera-track): its poses, in the order of their times. */
static void read_track(void) {
    char line[256];
    FILE *f;
    free(track);
    track = NULL, n_track = 0;
    if (!track_file[0] || !(f = fopen(track_file, "r"))) return;
    track = malloc(sizeof(struct pose) * MAX_TRACK);
    while (track && n_track < MAX_TRACK && fgets(line, sizeof line, f)) {
        struct pose q;
        if (sscanf(line, "%lf,%d,%lf,%lf,%lf,%lf,%lf,%lf,%lf", &q.t, &q.ext, &q.a, &q.b, &q.c, &q.h, &q.p, &q.r,
                   &q.fov) == 9)
            track[n_track++] = q;
    }
    fclose(f);
}

static double angle_between(double from, double to, double f) {
    double d = fmod(to - from + 540.0, 360.0) - 180.0;
    return from + d * f;
}

/* The recorded camera at t: between the two poses round it; outside or inside as the first. */
static void track_at(double t, struct pose *out) {
    int lo = 0, hi = n_track - 1;
    double f;
    const struct pose *a, *b;
    if (t <= track[0].t || n_track == 1) {
        *out = track[0];
        return;
    }
    if (t >= track[hi].t) {
        *out = track[hi];
        return;
    }
    while (hi - lo > 1) {
        int mid = (lo + hi) / 2;
        if (track[mid].t <= t) lo = mid;
        else hi = mid;
    }
    a = &track[lo], b = &track[hi];
    f = (t - a->t) / (b->t - a->t);
    *out = *a;
    if (a->ext != b->ext) return;
    out->a = a->a + (b->a - a->a) * f, out->b = a->b + (b->b - a->b) * f, out->c = a->c + (b->c - a->c) * f;
    out->h = angle_between(a->h, b->h, f), out->p = a->p + (b->p - a->p) * f;
    out->r = angle_between(a->r, b->r, f), out->fov = a->fov + (b->fov - a->fov) * f;
}

/* The recorded camera's time for the path's: looped, a recording shorter than the path played again
   from its start. */
static double track_clock(void) {
    double period = track[n_track - 1].t + TRACK_EVERY;
    return period > 0.0 ? fmod(path_t, period) : 0.0;
}

/* The user's camera, as X-Plane shows it now: a pose (struct pose). */
static void camera_pose(struct pose *q) {
    double psi = XPLMGetDataf(r_psi), rad = psi * M_PI / 180.0;
    q->t = path_t, q->ext = XPLMGetDatai(r_ext) != 0, q->fov = XPLMGetDataf(r_fov);
    if (q->ext) {
        double dx = XPLMGetDataf(r_view_x) - XPLMGetDatad(r_x), dy = XPLMGetDataf(r_view_y) - XPLMGetDatad(r_y),
               dz = XPLMGetDataf(r_view_z) - XPLMGetDatad(r_z);
        q->a = dx * sin(rad) - dz * cos(rad); /* ahead: north is -z */
        q->b = dx * cos(rad) + dz * sin(rad); /* right */
        q->c = dy;
        q->h = fmod(XPLMGetDataf(r_vhdg) - psi + 540.0, 360.0) - 180.0;
        q->p = XPLMGetDataf(r_vpitch), q->r = XPLMGetDataf(r_vroll);
    } else {
        q->a = XPLMGetDataf(r_head[0]), q->b = XPLMGetDataf(r_head[1]), q->c = XPLMGetDataf(r_head[2]);
        q->h = XPLMGetDataf(r_head[3]), q->p = XPLMGetDataf(r_head[4]), q->r = XPLMGetDataf(r_head[5]);
    }
}

static void record_camera(void) {
    struct pose q;
    if (!camfile || path_t - cam_last < TRACK_EVERY) return;
    cam_last = path_t;
    camera_pose(&q);
    fprintf(camfile, "%.2f,%d,%.3f,%.3f,%.3f,%.2f,%.2f,%.2f,%.1f\n", q.t, q.ext, q.a, q.b, q.c, q.h, q.p, q.r,
            q.fov);
}

/* The recorded camera's outside poses: from the aircraft where the path has it now. */
static int track_camera(XPLMCameraPosition_t *out, int losing, void *refcon) {
    struct pose q;
    double lat, lon, alt, hdg, x, y, z, rad;
    (void)refcon;
    if (losing || !out || state != FLYING || !replaying) {
        camera_on = 0;
        return 0;
    }
    track_at(track_clock(), &q);
    if (!q.ext) {
        camera_on = 0;
        return 0; /* inside: X-Plane's cockpit, the head set each frame (replay_camera) */
    }
    target(path_t, &lat, &lon, &alt, &hdg);
    XPLMWorldToLocal(lat, lon, alt, &x, &y, &z);
    rad = hdg * M_PI / 180.0;
    out->x = (float)(x + q.a * sin(rad) + q.b * cos(rad));
    out->y = (float)(y + q.c);
    out->z = (float)(z - q.a * cos(rad) + q.b * sin(rad));
    out->heading = (float)(hdg + q.h);
    out->pitch = (float)q.p;
    out->roll = (float)q.r;
    out->zoom = 1.0f;
    return 1;
}

/* Each frame of a replay: outside, the camera the plugin holds; inside, the 3D cockpit and the
   pilot's head where the user had it; the field of view either way. */
static void replay_camera(void) {
    struct pose q;
    track_at(track_clock(), &q);
    if (q.fov > 1.0) XPLMSetDataf(r_fov, (float)q.fov);
    if (q.ext) {
        inside_set = 0;
        if (!camera_on) {
            XPLMCommandOnce(XPLMFindCommand("sim/view/circle"));
            XPLMControlCamera(XPLM_CONTROL_CAMERA_FOREVER, track_camera, NULL);
            camera_on = 1;
        }
        return;
    }
    if (camera_on) {
        XPLMDontControlCamera();
        camera_on = 0;
    }
    if (!inside_set) {
        XPLMCommandOnce(XPLMFindCommand("sim/view/3d_cockpit_cmnd_look"));
        inside_set = 1;
    }
    XPLMSetDataf(r_head[0], (float)q.a), XPLMSetDataf(r_head[1], (float)q.b), XPLMSetDataf(r_head[2], (float)q.c);
    XPLMSetDataf(r_head[3], (float)q.h), XPLMSetDataf(r_head[4], (float)q.p), XPLMSetDataf(r_head[5], (float)q.r);
}

static const struct camera *camera_at(double t) {
    int i;
    for (i = 0; i < n_cameras; i++)
        if (t >= cameras[i].t0 && t < cameras[i].t1 && cameras[i].chase) return &cameras[i];
    return NULL;
}

/* The 3D cockpit at the start of each of the scenario's cockpit cameras: the same view every run,
   whatever view X-Plane was left in. */
static void cockpit_view(double t) {
    int i;
    for (i = 0; i < n_cameras; i++)
        if (t >= cameras[i].t0 && t < cameras[i].t1 && !cameras[i].chase && cockpit_set != i) {
            cockpit_set = i;
            XPLMCommandOnce(XPLMFindCommand("sim/view/3d_cockpit_cmnd_look"));
        }
}

static int chase_camera(XPLMCameraPosition_t *out, int losing, void *refcon) {
    const struct camera *cam = camera_at(path_t);
    double lat, lon, alt, hdg, b, x, y, z, cx, cz, dx, dy, dz;
    (void)refcon;
    if (losing || !out || !cam || state != FLYING) {
        camera_on = 0;
        return 0; /* X-Plane's own view again */
    }
    target(path_t, &lat, &lon, &alt, &hdg);
    XPLMWorldToLocal(lat, lon, alt, &x, &y, &z);
    b = (hdg + cam->bearing) * M_PI / 180.0;
    cx = x + cam->dist * sin(b); /* east: +x; north: -z */
    cz = z - cam->dist * cos(b);
    out->x = (float)cx;
    out->y = (float)(y + cam->up);
    out->z = (float)cz;
    dx = x - cx, dy = -cam->up, dz = z - cz;
    out->heading = (float)(atan2(dx, -dz) * 180.0 / M_PI);
    out->pitch = (float)(atan2(dy, hypot(dx, dz)) * 180.0 / M_PI);
    out->roll = 0.0f;
    out->zoom = 1.0f;
    return 1;
}

/* A scenery load starting, marked in Log.txt before the DSFs it brings (the header). */
static void watch_loads(void) {
    int now_loading = XPLMGetDatai(r_loading) != 0;
    double run = XPLMGetDataf(r_run);
    if (now_loading && !loading) {
        load_began = run;
        say("scenery load at %.4f %.4f, path %.1f s", XPLMGetDatad(r_lat), XPLMGetDatad(r_lon),
            state == FLYING ? path_t : -1.0);
    } else if (!now_loading && loading) {
        say("scenery loaded in %.1f s", run - load_began);
    }
    loading = now_loading;
}

static void record(float dt) {
    double t = XPLMGetDataf(r_run);
    if (!frames) return;
    fprintf(frames, "%.4f,%.2f,%.4f,%.6f,%.6f,%.1f,%d,%.1f,%.1f,%.1f,%d\n", t, path_t, dt * 1000.0,
            XPLMGetDatad(r_lat), XPLMGetDatad(r_lon), XPLMGetDatad(r_ele), state, XPLMGetDataf(r_view_x),
            XPLMGetDataf(r_view_y), XPLMGetDataf(r_view_z), loading);
    if (t - frame_flushed > 1.0 || t < frame_flushed) {
        fflush(frames);
        frame_flushed = t;
    }
}

static void path_of(char *out, size_t size, const char *name) { snprintf(out, size, "%s%s", box, name); }

static void rename_scenario(void) {
    char from[1200], to[1200];
    path_of(from, sizeof from, "scenario.txt");
    path_of(to, sizeof to, "scenario.done");
    rename(from, to);
    scenario_time = 0; /* the next scenario.txt is new, even written within the same second */
}

static long file_time(const char *name) {
    char file[1200];
    struct stat st;
    path_of(file, sizeof file, name);
    return stat(file, &st) == 0 ? (long)st.st_mtime : 0;
}

/* A new scenario: read, its path made, its files opened, the flight started from the next frame. */
static void begin(void) {
    char file[1200];
    path_of(file, sizeof file, "scenario.txt");
    if (frames) fclose(frames);
    if (events) fclose(events);
    frames = events = NULL;
    free(path);
    path = NULL;
    state = WAITING, placed_frames = 0, ready_since = -1.0, camera_on = 0, cockpit_set = -1, loading = 0;
    inside_set = 0, cam_last = -1.0, replaying = 0;
    if (camfile) fclose(camfile);
    camfile = NULL;
    if (!read_scenario(file)) {
        active = 0;
        say("scenario.txt read: no start or no leg");
        rename_scenario();
        return;
    }
    make_path();
    read_track();
    if (camera_record) {
        path_of(file, sizeof file, "camera.csv");
        camfile = fopen(file, "w");
        if (camfile) fprintf(camfile, "path_s,external,a,b,c,h,p,r,fov\n");
    }
    path_of(file, sizeof file, "events.txt");
    events = fopen(file, "w");
    path_of(file, sizeof file, "frames.csv");
    frames = fopen(file, "w");
    if (frames) {
        fprintf(frames, "run_s,path_s,frame_ms,lat,lon,ele_m,state,view_x,view_y,view_z,loading\n");
        fflush(frames);
    }
    say("scenario read: start %.4f %.4f, %d legs, %d cameras, %d screenshots", start_lat, start_lon, n_legs,
        n_cameras, n_shots);
    if (camera_record) say("the user's camera recorded: the view is theirs");
    if (track_file[0]) say("a recorded camera replayed: %d poses from %s", n_track, track_file);
}

static double wall_now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* X-Plane asked to quit: it asks "Quit the simulator?" (the header). */
static void quit_xplane(void) { XPLMCommandOnce(XPLMFindCommand("sim/operation/quit")); }

/* Every second of the wall clock (X-Plane's stands still while it is paused): what the runner asks
   (the header's list). No reload of the plugins: FlyWithLua crashed X-Plane on one (2026-09-29). */
static void poll_box(void) {
    char file[1200];
    long t;
    double wall = wall_now();
    if (wall - polled < 1.0) return;
    polled = wall;
    if (file_time("quit")) {
        path_of(file, sizeof file, "quit");
        unlink(file);
        say("quitting X-Plane, as asked");
        quit_xplane();
        return;
    }
    t = file_time("scenario.txt");
    if (t && t != scenario_time && (state == WAITING || state == DONE || !active)) {
        scenario_time = t;
        begin();
    }
}

static void finish(void) {
    int zero = 0;
    state = DONE;
    if (camera_on) XPLMDontControlCamera();
    XPLMCommandOnce(XPLMFindCommand("sim/operation/pause_on")); /* first: the aircraft has no speed of its own */
    XPLMSetDatavi(r_override, &zero, 0, 1);
    say("the path flown (%.0f s)", path_t);
    say("done");
    if (frames) fclose(frames);
    frames = NULL;
    if (camfile) fclose(camfile);
    camfile = NULL;
    rename_scenario();
    if (end_quit) {
        say("quitting X-Plane");
        quit_xplane();
    }
}

static float every_frame(float since_call, float since_loop, int counter, void *refcon) {
    double run = XPLMGetDataf(r_run), lat = XPLMGetDatad(r_lat), lon = XPLMGetDatad(r_lon);
    (void)since_loop;
    (void)counter;
    (void)refcon;
    poll_box();
    if (!active || state == DONE) return -1.0f;
    watch_loads();
    if (state == WAITING) { /* X-Plane's first frames: the flight starts where the scenario says */
        if (++placed_frames < 10) return -1.0f;
        if (weather_clear) set_weather();
        if (day >= 0) XPLMSetDatai(XPLMFindDataRef("sim/time/local_date_days"), (int)day);
        if (zulu_h >= 0) XPLMSetDataf(XPLMFindDataRef("sim/time/zulu_time_sec"), (float)(zulu_h * 3600.0));
        if (km(lat, lon, start_lat, start_lon) < START_KM && fabs(XPLMGetDatad(r_ele) - start_ft * FT) < 300.0) {
            say("already at the start: not placed again"); /* no second load of the scenery */
        } else {
            XPLMPlaceUserAtLocation(start_lat, start_lon, (float)(start_ft * FT), (float)start_hdg,
                                    (float)(start_kt * KT));
            say("placed at %.4f %.4f, %.0f ft, heading %.0f, %.0f kt", start_lat, start_lon, start_ft, start_hdg,
                start_kt);
        }
        state = PLACED;
        return -1.0f;
    }
    if (state == PLACED) { /* X-Plane flies there: its clock runs, the aircraft at the start */
        int there = km(lat, lon, start_lat, start_lon) < START_KM;
        if (there && XPLMGetDatai(r_paused) != 0) { /* X-Plane pauses after a start in the air */
            static double unpaused = -10.0;
            double wall = wall_now();
            if (fabs(wall - unpaused) > 1.0) {
                unpaused = wall;
                XPLMCommandOnce(XPLMFindCommand("sim/operation/pause_off"));
            }
        }
        there = there && XPLMGetDatai(r_paused) == 0;
        if (!there) {
            ready_since = -1.0;
            return -1.0f;
        }
        if (ready_since < 0) ready_since = run;
        if (run - ready_since < READY_S) return -1.0f;
        if (weather_clear) set_weather(); /* again: the new flight's weather */
        run0 = run;
        state = FLYING;
        say("flying the path: %d legs, %.0f s", n_legs, (n_path - 1) * STEP);
    }
    path_t = run - run0; /* X-Plane's clock: a pause, or a hold, stops the path too */
    if (path_t >= (n_path - 1) * STEP) {
        finish();
        return -1.0f;
    }
    place(path_t);
    replaying = n_track > 0;
    if (camera_record) {
        record_camera(); /* the view is the user's */
    } else if (replaying) {
        replay_camera();
    } else {
        cockpit_view(path_t);
    }
    if (!camera_record && !replaying && !camera_on && camera_at(path_t)) {
        /* an outside view first: X-Plane then draws the aircraft, not its cockpit round the camera */
        XPLMCommandOnce(XPLMFindCommand("sim/view/circle"));
        XPLMControlCamera(XPLM_CONTROL_CAMERA_FOREVER, chase_camera, NULL);
        camera_on = 1;
    }
    while (next_shot < n_shots && path_t >= shots[next_shot]) {
        XPLMCommandOnce(XPLMFindCommand("sim/operation/screenshot"));
        say("screenshot at %.1f s", shots[next_shot]);
        next_shot++;
    }
    record(since_call);
    return -1.0f;
}

PLUGIN_API int XPluginStart(char *outName, char *outSig, char *outDesc) {
    strcpy(outName, "OrthoStudio XP Test Pilot");
    strcpy(outSig, "orthostudio.live.testpilot");
    strcpy(outDesc, "Flies OrthoStudio XP Live's replayable test flights; does nothing without a scenario.");
    r_lat = XPLMFindDataRef("sim/flightmodel/position/latitude");
    r_lon = XPLMFindDataRef("sim/flightmodel/position/longitude");
    r_ele = XPLMFindDataRef("sim/flightmodel/position/elevation");
    r_run = XPLMFindDataRef("sim/time/total_running_time_sec");
    r_paused = XPLMFindDataRef("sim/time/paused");
    r_x = XPLMFindDataRef("sim/flightmodel/position/local_x");
    r_y = XPLMFindDataRef("sim/flightmodel/position/local_y");
    r_z = XPLMFindDataRef("sim/flightmodel/position/local_z");
    r_vx = XPLMFindDataRef("sim/flightmodel/position/local_vx");
    r_vy = XPLMFindDataRef("sim/flightmodel/position/local_vy");
    r_vz = XPLMFindDataRef("sim/flightmodel/position/local_vz");
    r_psi = XPLMFindDataRef("sim/flightmodel/position/psi");
    r_theta = XPLMFindDataRef("sim/flightmodel/position/theta");
    r_phi = XPLMFindDataRef("sim/flightmodel/position/phi");
    r_q = XPLMFindDataRef("sim/flightmodel/position/q");
    r_override = XPLMFindDataRef("sim/operation/override/override_planepath");
    r_view_x = XPLMFindDataRef("sim/graphics/view/view_x");
    r_view_y = XPLMFindDataRef("sim/graphics/view/view_y");
    r_view_z = XPLMFindDataRef("sim/graphics/view/view_z");
    r_loading = XPLMFindDataRef("sim/graphics/scenery/async_scenery_load_in_progress");
    r_ext = XPLMFindDataRef("sim/graphics/view/view_is_external");
    r_vhdg = XPLMFindDataRef("sim/graphics/view/view_heading");
    r_vpitch = XPLMFindDataRef("sim/graphics/view/view_pitch");
    r_vroll = XPLMFindDataRef("sim/graphics/view/view_roll");
    r_fov = XPLMFindDataRef("sim/graphics/view/field_of_view_deg");
    r_head[0] = XPLMFindDataRef("sim/graphics/view/pilots_head_x");
    r_head[1] = XPLMFindDataRef("sim/graphics/view/pilots_head_y");
    r_head[2] = XPLMFindDataRef("sim/graphics/view/pilots_head_z");
    r_head[3] = XPLMFindDataRef("sim/graphics/view/pilots_head_psi");
    r_head[4] = XPLMFindDataRef("sim/graphics/view/pilots_head_the");
    r_head[5] = XPLMFindDataRef("sim/graphics/view/pilots_head_phi");
    XPLMEnableFeature("XPLM_USE_NATIVE_PATHS", 1); /* on the Mac, "/Users/..." rather than "Macintosh HD:..." */
    XPLMGetSystemPath(dir);
    snprintf(box, sizeof box, "%sOutput/osxp_test_pilot/", dir);
    say("ready: scenarios in %s", box);
    return 1;
}

PLUGIN_API void XPluginStop(void) {
    if (frames) fclose(frames);
    if (events) fclose(events);
    if (camfile) fclose(camfile);
    frames = events = camfile = NULL;
    free(track);
    track = NULL;
    free(path);
    path = NULL;
}

PLUGIN_API int XPluginEnable(void) {
    XPLMRegisterFlightLoopCallback(every_frame, -1.0f, NULL);
    return 1;
}

PLUGIN_API void XPluginDisable(void) {
    int zero = 0;
    XPLMUnregisterFlightLoopCallback(every_frame, NULL);
    if (camera_on) XPLMDontControlCamera();
    if (active && state == FLYING) XPLMSetDatavi(r_override, &zero, 0, 1);
}

PLUGIN_API void XPluginReceiveMessage(XPLMPluginID inFrom, int inMsg, void *inParam) {
    (void)inFrom;
    (void)inMsg;
    (void)inParam;
}
