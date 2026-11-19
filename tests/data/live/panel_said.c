/* A stand-in for the plugin's loading window (native/xplane_plugin/live_panel*.{m,c}) that prints
   what it is told to show, one line each time: "PANEL what | progress | fraction". Built with
   native/xplane_plugin/live_gate.c and tests/data/live/fake_xplm.c, it lets the tests read the
   pilot's words without a screen (tests/test_live_gate.py). */

#include <stdio.h>

void live_panel_show(const char *what, const char *progress, double fraction) {
    printf("PANEL %s | %s | %.3f\n", what, progress, fraction);
    fflush(stdout);
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
#ifndef __APPLE__
void live_panel_tick(void) {}
void live_panels_close(void) {}
#endif
