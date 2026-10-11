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
 *
 * In flight, a second panel, small in the top right corner, shows the streamer's notice (no mesh
 * here, the region to install) with its cross. X-Plane runs then, so the cross works as any button:
 * it takes a click at once, and the panel never takes the keyboard from X-Plane. (It offered a
 * reload of the scenery too, for the 8192 px textures built after a start, until a test showed
 * that X-Plane's reload does not read again the textures it holds, 2026-09-27.)
 */

#import <Cocoa/Cocoa.h>
#import <QuartzCore/QuartzCore.h>
#include <stdio.h>
#include <string.h>

static NSPanel *panel;
static NSTextField *headline, *detail, *building_line;
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

void live_panel_show(const char *what, const char *progress, const char *building, double fraction) {
    if (NSApp == nil) return;
    @autoreleasepool {
        if (panel == nil) {
            NSWindow *xp = xplane_window();
            NSRect around = xp ? xp.frame : [NSScreen mainScreen].frame;
            NSRect r = NSMakeRect(NSMidX(around) - 280, NSMidY(around) - 70, 560, 140);
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

            NSTextField *title = label(NSMakeRect(20, 108, 520, 20), 12, [NSColor colorWithCalibratedWhite:0.6 alpha:1]);
            title.stringValue = @"OrthoStudio XP Live";
            headline = label(NSMakeRect(20, 78, 520, 26), 17, [NSColor whiteColor]);
            detail = label(NSMakeRect(20, 56, 520, 20), 13, [NSColor colorWithCalibratedWhite:0.8 alpha:1]);
            building_line = label(NSMakeRect(20, 36, 520, 20), 13, [NSColor colorWithCalibratedWhite:0.8 alpha:1]);
            for (NSView *v in @[ title, headline, detail, building_line ]) [box addSubview:v];
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
        headline.stringValue = [NSString stringWithUTF8String:what] ?: @"";
        detail.stringValue = [NSString stringWithUTF8String:progress] ?: @"";
        building_line.stringValue = [NSString stringWithUTF8String:building ? building : ""] ?: @"";
        [CATransaction begin];
        [CATransaction setDisableActions:YES]; /* no animation: nothing would run it */
        fill.frame = CGRectMake(0, 0, BAR_W * (fraction < 0 ? 0 : fraction > 1 ? 1 : fraction), BAR_H);
        [CATransaction commit];
        [panel display];
        [CATransaction flush];
    }
}

/* --- the notice ------------------------------------------------------------------------------ */

static NSPanel *note_panel;
static int note_closed; /* its cross clicked */

void live_note_press(void) { note_closed = 1; } /* its cross; the tests call it too */

int live_note_closed(void) {
    int c = note_closed;
    note_closed = 0;
    return c;
}

@interface LiveNoteTarget : NSObject
@end
@implementation LiveNoteTarget
- (void)close:(id)sender {
    live_note_press();
}
@end

/* A click goes through at once, though the panel is not the key window: X-Plane keeps the keyboard. */
@interface LiveFirstClickButton : NSButton
@end
@implementation LiveFirstClickButton
- (BOOL)acceptsFirstMouse:(NSEvent *)event {
    return YES;
}
@end

static LiveNoteTarget *note_target;

void live_note_hide(void);

static char note_text[160]; /* what the panel shows now, in French or not */
static int note_french;

void live_note_show(const char *what, int french) {
    if (NSApp == nil) return;
    if (note_panel != nil && note_french == french && strcmp(note_text, what) == 0) return;
    live_note_hide();
    snprintf(note_text, sizeof note_text, "%s", what);
    note_french = french;
    @autoreleasepool {
        /* small, in the top right corner of X-Plane's window, below its menu bar's band; as wide as
           its words */
        NSString *words = [NSString stringWithUTF8String:what] ?: @"";
        NSFont *font = [NSFont systemFontOfSize:13];
        CGFloat text_w = MIN(560, MAX(270, ceil([words sizeWithAttributes:@{NSFontAttributeName : font}].width) + 8));
        const CGFloat h = 44;
        CGFloat w = 14 + text_w + 34;
        NSWindow *xp = xplane_window();
        NSRect around = xp ? xp.frame : [NSScreen mainScreen].frame;
        NSRect r = NSMakeRect(NSMaxX(around) - w - 16, NSMaxY(around) - h - 48, w, h);
        note_panel = [[NSPanel alloc] initWithContentRect:r
                                                 styleMask:NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
                                                   backing:NSBackingStoreBuffered
                                                     defer:NO];
        note_panel.level = MAX(NSStatusWindowLevel, (xp ? xp.level : 0) + 1);
        note_panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary;
        note_panel.opaque = NO;
        note_panel.backgroundColor = [NSColor clearColor];
        note_panel.hasShadow = YES;
        note_panel.releasedWhenClosed = NO;
        note_panel.becomesKeyOnlyIfNeeded = YES;

        NSView *box = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, w, h)];
        box.wantsLayer = YES;
        box.layer.backgroundColor = [NSColor colorWithCalibratedWhite:0.08 alpha:0.92].CGColor;
        box.layer.cornerRadius = 10;
        note_panel.contentView = box;

        NSTextField *text = label(NSMakeRect(14, 12, text_w, 20), 13, [NSColor whiteColor]);
        text.alignment = NSTextAlignmentLeft;
        text.stringValue = words;
        note_target = [LiveNoteTarget new];
        [box addSubview:text];
        LiveFirstClickButton *close = [[LiveFirstClickButton alloc] initWithFrame:NSMakeRect(w - 32, 9, 26, 26)];
        close.target = note_target;
        close.action = @selector(close:);
        close.bordered = NO;
        close.attributedTitle = [[NSAttributedString alloc]
            initWithString:@"\u00d7"
                attributes:@{
                    NSForegroundColorAttributeName : [NSColor colorWithCalibratedWhite:0.75 alpha:1],
                    NSFontAttributeName : [NSFont systemFontOfSize:17]
                }];
        close.toolTip = french ? @"Fermer" : @"Close";
        close.accessibilityLabel = close.toolTip;
        [box addSubview:close];
        [note_panel orderFrontRegardless];
    }
}

void live_note_hide(void) {
    if (note_panel == nil) return;
    @autoreleasepool {
        [note_panel orderOut:nil];
        note_panel = nil;
        note_target = nil;
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
