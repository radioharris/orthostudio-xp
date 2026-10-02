/* A stand-in for X-Plane itself, for the test flights' runner (tools/live/flights/fly.py) on
   Windows and Linux: built as X-Plane.exe or X-Plane-x86_64 into a folder made as X-Plane's, with
   the XPLM functions of fake_xplm.c (built with -DNO_MAIN; on Windows a fake XPLM_64.dll beside
   it). As X-Plane it:
   - takes X-Plane's command line: --load_llah=LAT,LON,FEET,HEADING puts the aircraft there;
   - loads every plugin of Resources/plugins/<name>/win_x64 or lin_x64, and starts it;
   - writes what the plugins say to Log.txt, beside it;
   - calls their flight loops, a frame every 20 ms;
   - quits at the command sim/operation/quit, or at "CMND\0sim/operation/quit" on UDP 49000,
     stopping its plugins first;
   - moves the aircraft where a plugin sets its local position (local_x, local_z), from the
     place it started.
   Nothing else: no scenery, no RREF answers. */

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#define SEP "\\"
#define PLATFORM "win_x64"
#else
#include <arpa/inet.h>
#include <dirent.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#define SEP "/"
#define PLATFORM "lin_x64"
#endif
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

void fake_position(double lat, double lon); /* fake_xplm.c */
void fake_elevation(double metres);
int fake_loops(int counter);
int fake_quit_asked(void);
void fake_log_to(const char *path);

typedef int (*start_f)(char *, char *, char *);
typedef int (*enable_f)(void);
typedef void (*plain_f)(void);

#define MAX_PLUGINS 8
static struct {
    plain_f disable, stop;
} plugins[MAX_PLUGINS];
static int n_plugins = 0;

static void *load(const char *path) {
#ifdef _WIN32
    wchar_t w[2400];
    if (!MultiByteToWideChar(CP_UTF8, 0, path, -1, w, 2400)) return NULL;
    return (void *)LoadLibraryW(w);
#else
    return dlopen(path, RTLD_NOW | RTLD_LOCAL);
#endif
}

static void *find(void *lib, const char *name) {
#ifdef _WIN32
    return (void *)GetProcAddress((HMODULE)lib, name);
#else
    return dlsym(lib, name);
#endif
}

/* A plugin's file, started and enabled as X-Plane does. */
static void start_plugin(const char *path) {
    char name[256], sig[256], desc[256];
    void *lib = load(path);
    start_f start;
    enable_f enable;
    if (!lib || n_plugins >= MAX_PLUGINS) {
        fprintf(stderr, "not loaded: %s\n", path);
        return;
    }
    start = (start_f)find(lib, "XPluginStart");
    enable = (enable_f)find(lib, "XPluginEnable");
    plugins[n_plugins].disable = (plain_f)find(lib, "XPluginDisable");
    plugins[n_plugins].stop = (plain_f)find(lib, "XPluginStop");
    if (!start || !enable || !plugins[n_plugins].disable || !plugins[n_plugins].stop) return;
    if (start(name, sig, desc) == 1 && enable() == 1) n_plugins++;
}

/* Every plugin of root/Resources/plugins/<name>/<platform>/<name>.xpl. */
static void start_plugins(const char *root) {
    char folder[1100], path[2400]; /* room for the folder and two names, as gcc reckons it */
    snprintf(folder, sizeof folder, "%sResources" SEP "plugins", root);
#ifdef _WIN32
    {
        wchar_t pattern[1200], wfolder[1200];
        WIN32_FIND_DATAW found;
        HANDLE h;
        MultiByteToWideChar(CP_UTF8, 0, folder, -1, wfolder, 1200);
        swprintf(pattern, 1200, L"%ls\\*", wfolder);
        h = FindFirstFileW(pattern, &found);
        if (h == INVALID_HANDLE_VALUE) return;
        do {
            char name[600];
            if (!(found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || found.cFileName[0] == L'.') continue;
            WideCharToMultiByte(CP_UTF8, 0, found.cFileName, -1, name, sizeof name, NULL, NULL);
            snprintf(path, sizeof path, "%s" SEP "%s" SEP PLATFORM SEP "%s.xpl", folder, name, name);
            {
                wchar_t wpath[2400];
                FILE *f;
                MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, 2400);
                if ((f = _wfopen(wpath, L"rb")) != NULL) {
                    fclose(f);
                    start_plugin(path);
                }
            }
        } while (FindNextFileW(h, &found));
        FindClose(h);
    }
#else
    {
        DIR *d = opendir(folder);
        struct dirent *e;
        if (!d) return;
        while ((e = readdir(d)) != NULL) {
            FILE *f;
            if (e->d_name[0] == '.') continue;
            snprintf(path, sizeof path, "%s/%s/" PLATFORM "/%s.xpl", folder, e->d_name, e->d_name);
            if ((f = fopen(path, "rb")) != NULL) {
                fclose(f);
                start_plugin(path);
            }
        }
        closedir(d);
    }
#endif
}

/* The folder this program is in, with its separator, in UTF-8. */
static void own_folder(char *out, size_t size) {
#ifdef _WIN32
    wchar_t w[1024];
    DWORD n = GetModuleFileNameW(NULL, w, 1024);
    char *cut;
    out[0] = 0;
    if (n == 0 || n >= 1024) return;
    WideCharToMultiByte(CP_UTF8, 0, w, -1, out, (int)size, NULL, NULL);
    cut = strrchr(out, '\\');
    if (cut) cut[1] = 0;
#else
    ssize_t n = readlink("/proc/self/exe", out, size - 1);
    char *cut;
    out[n > 0 ? n : 0] = 0;
    cut = strrchr(out, '/');
    if (cut) cut[1] = 0;
#endif
}

static void pause_ms(int ms) {
#ifdef _WIN32
    Sleep((DWORD)ms);
#else
    usleep(ms * 1000);
#endif
}

int main(int argc, char **argv) {
    char root[1024], log[1200], packet[512];
    double lat = 0, lon = 0, feet = 0, heading = 0;
    int i, frame = 0;
#ifdef _WIN32
    SOCKET udp;
    WSADATA wsa;
    u_long on = 1;
    wchar_t wroot[1024];
    WSAStartup(MAKEWORD(2, 2), &wsa);
#else
    int udp;
#endif
    struct sockaddr_in addr;
    for (i = 1; i < argc; i++) sscanf(argv[i], "--load_llah=%lf,%lf,%lf,%lf", &lat, &lon, &feet, &heading);
    own_folder(root, sizeof root);
#ifdef _WIN32
    MultiByteToWideChar(CP_UTF8, 0, root, -1, wroot, 1024);
    wroot[wcslen(wroot) - 1] = 0; /* without its separator, as the variable has it */
    SetEnvironmentVariableW(L"FAKE_XPLANE", wroot);
#else
    {
        char bare[1024];
        snprintf(bare, sizeof bare, "%s", root);
        bare[strlen(bare) - 1] = 0;
        setenv("FAKE_XPLANE", bare, 1);
    }
#endif
    snprintf(log, sizeof log, "%sLog.txt", root);
    fake_log_to(log);
    fake_position(lat, lon);
    fake_elevation(feet * 0.3048);
    udp = socket(AF_INET, SOCK_DGRAM, 0);
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons(49000);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    bind(udp, (struct sockaddr *)&addr, sizeof addr);
#ifdef _WIN32
    ioctlsocket(udp, FIONBIO, &on);
#else
    fcntl(udp, F_SETFL, fcntl(udp, F_GETFL, 0) | O_NONBLOCK);
#endif
    start_plugins(root);
    for (;;) {
        int n = (int)recv(udp, packet, sizeof packet - 1, 0);
        if (n > 5 && memcmp(packet, "CMND", 4) == 0 && strstr(packet + 5, "sim/operation/quit")) break;
        if (fake_quit_asked()) break;
        fake_loops(++frame);
        pause_ms(20);
    }
    for (i = n_plugins - 1; i >= 0; i--) {
        plugins[i].disable();
        plugins[i].stop();
    }
    return 0;
}
