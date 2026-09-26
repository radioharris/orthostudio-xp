/* OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
 * Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE. */

/*
 * The plugin's own loading window, shown over X-Plane while the plugin holds it
 * (live_gate.c).
 *
 * X-Plane's main thread is held, so its window is not redrawn, and at a flight change it still
 * shows the Flight Configuration screen. This small panel says what X-Plane is waiting for.
 *
 * The run loop does not turn while the thread is held, so the panel is drawn on the spot: every
 * update calls -display and then [CATransaction flush], which sends the drawing to the window
 * server. The progress bar is two plain layers, sized without animation. NSProgressIndicator
 * stayed empty: it needs the run loop to draw a new value. Outside an application (the tests'
 * stand-in for X-Plane), NSApp is nil and nothing is shown.
 */

#import <Cocoa/Cocoa.h>
#import <QuartzCore/QuartzCore.h>

static NSPanel *panel;
static NSTextField *headline, *detail;
static CALayer *track, *fill;

#define BAR_W 480.0
#define BAR_H 8.0

static NSWindow *xplane_window(void) {
    NSWindow *best = nil;
    for (NSWindow *w in [NSApp windows]) {
        if (w.visible && (best == nil || w.frame.size.width * w.frame.size.height > best.frame.size.width * best.frame.size.height))
            best = w;
    }
    return best;
}

static NSTextField *label(NSRect frame, CGFloat size, NSColor *color) {
    NSTextField *t = [NSTextField labelWithString:@""];
    t.frame = frame;
    t.font = [NSFont systemFontOfSize:size];
    t.textColor = color;
    t.alignment = NSTextAlignmentCenter;
    t.lineBreakMode = NSLineBreakByTruncatingTail;
    return t;
}

void live_panel_show(const char *what, const char *progress, double fraction) {
    if (NSApp == nil) return;
    @autoreleasepool {
        if (panel == nil) {
            NSWindow *xp = xplane_window();
            NSRect around = xp ? xp.frame : [NSScreen mainScreen].frame;
            NSRect r = NSMakeRect(NSMidX(around) - 280, NSMidY(around) - 60, 560, 120);
            panel = [[NSPanel alloc] initWithContentRect:r
                                               styleMask:NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
                                                 backing:NSBackingStoreBuffered
                                                   defer:NO];
            panel.level = MAX(NSStatusWindowLevel, (xp ? xp.level : 0) + 1);
            panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary;
            panel.opaque = NO;
            panel.backgroundColor = [NSColor clearColor];
            panel.hasShadow = YES;
            panel.releasedWhenClosed = NO;

            NSView *box = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, r.size.width, r.size.height)];
            box.wantsLayer = YES;
            box.layer.backgroundColor = [NSColor colorWithCalibratedWhite:0.08 alpha:0.92].CGColor;
            box.layer.cornerRadius = 12;
            panel.contentView = box;

            NSTextField *title = label(NSMakeRect(20, 88, 520, 20), 12, [NSColor colorWithCalibratedWhite:0.6 alpha:1]);
            title.stringValue = @"OrthoStudio XP Live";
            headline = label(NSMakeRect(20, 58, 520, 26), 17, [NSColor whiteColor]);
            detail = label(NSMakeRect(20, 36, 520, 20), 13, [NSColor colorWithCalibratedWhite:0.8 alpha:1]);
            for (NSView *v in @[ title, headline, detail ]) [box addSubview:v];
            track = [CALayer layer];
            track.frame = CGRectMake((r.size.width - BAR_W) / 2, 16, BAR_W, BAR_H);
            track.backgroundColor = [NSColor colorWithCalibratedWhite:0.3 alpha:1].CGColor;
            track.cornerRadius = BAR_H / 2;
            fill = [CALayer layer];
            fill.frame = CGRectMake(0, 0, 0, BAR_H);
            fill.backgroundColor = [NSColor colorWithCalibratedRed:0.25 green:0.6 blue:1.0 alpha:1].CGColor;
            fill.cornerRadius = BAR_H / 2;
            [track addSublayer:fill];
            [box.layer addSublayer:track];
            [panel orderFrontRegardless];
        }
        headline.stringValue = [NSString stringWithUTF8String:what];
        detail.stringValue = [NSString stringWithUTF8String:progress];
        [CATransaction begin];
        [CATransaction setDisableActions:YES]; /* no animation: nothing would run it */
        fill.frame = CGRectMake(0, 0, BAR_W * (fraction < 0 ? 0 : fraction > 1 ? 1 : fraction), BAR_H);
        [CATransaction commit];
        [panel display];
        [CATransaction flush];
    }
}

void live_panel_hide(void) {
    if (panel == nil) return;
    @autoreleasepool {
        [panel orderOut:nil];
        panel = nil;
        headline = detail = nil;
        track = fill = nil;
        [CATransaction flush];
    }
}
