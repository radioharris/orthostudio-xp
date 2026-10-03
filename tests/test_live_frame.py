# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP and OrthoStudio XP Live in one window, the app's frame (the user's choice,
2026-10-04): its page served on the loopback with what it starts from, each part loaded the first
time it is shown and kept, what the pilot chose kept once for both, quitting asked once while a
build or a stream runs; and a page in the frame (``ui/embed.js``), which leaves it its bars."""

from __future__ import annotations

import http.client
import json
import shutil
import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orthostudio import desktop
from orthostudio import window as window_module
from orthostudio.live import frame as frame_module
from orthostudio.live import web
from orthostudio.live.app_start import LIVE_PORT
from orthostudio.live.frame import (
    FRAME_DIR,
    Frame,
    FrameTools,
    clean_prefs,
    frame_page,
    load_prefs,
    save_prefs,
    serve,
)
from orthostudio.live.settings import load_settings
from orthostudio.ui import ui_dir

NODE = shutil.which("node")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(web, "_here", None)  # no Live in this program, and none left after
    return tmp_path / "home"


def wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return bool(predicate())


# -- what the pilot chose --------------------------------------------------------------------------


def test_only_what_the_pages_know_is_kept() -> None:
    assert clean_prefs({"language": "de", "theme": "blue", "textSize": True, "zoom": 9}) == {}
    assert clean_prefs({"textSize": 130.0, "zoom": 1, "else": "x"}) == {
        "textSize": 130,
        "zoom": 1.0,
    }
    assert clean_prefs({"language": "fr", "theme": ""}) == {"language": "fr", "theme": ""}
    assert clean_prefs("nothing") == {}


def test_what_the_pilot_chose_is_kept_and_live_speaks_its_language(home: Path) -> None:
    frame = Frame(home / "serve.log", first="build")
    assert load_prefs() is None and frame.given()["kept"] is False  # the frame's first start
    chosen = {"language": "fr", "theme": "dark", "textSize": 115, "zoom": 1.25, "else": 1}
    kept = frame.keep(chosen)
    assert kept == {"language": "fr", "theme": "dark", "textSize": 115, "zoom": 1.25}
    given = frame.given()
    assert load_prefs() == kept and given["kept"] is True and given["prefs"] == kept
    # Live is not set up in this program: its settings, which its plugin in X-Plane reads
    assert load_settings().language == "fr"
    # set up: its control, which tells the plugin at once
    told: list[str] = []
    live = web._Starting()
    live.control = SimpleNamespace(set_language=told.append)
    web._be_here(live)
    frame.keep({"language": "en"})
    assert told == ["en"] and load_prefs() == {"language": "en"}
    (home / frame_module.PREFS_FILE).write_text("{not json")
    assert load_prefs() is None  # unreadable: as if never kept, the pages' own taken again


def test_the_page_is_given_what_it_starts_from() -> None:
    page = frame_page({"first": "live", "prefs": {"x": "</script><script>alert(1)"}}).decode()
    assert '"first": "live"' in page and "</script><script>alert" not in page
    given = page[page.index('id="frame-given">') :]
    assert given.index("</script>") > given.index("alert")  # still inside its own script
    assert '<script type="module" src="frame/frame.js"></script>' in page


# -- its page, served ------------------------------------------------------------------------------


def _get(port: int, path: str, host: str | None = None) -> tuple[int, str, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{port}"})
        answer = conn.getresponse()
        return answer.status, answer.getheader("Content-Type") or "", answer.read()
    finally:
        conn.close()


def test_the_frame_is_served_on_its_port_or_another_and_nothing_else() -> None:
    taken = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    port = taken.getsockname()[1]
    server, served = serve(lambda: b"<html>frame</html>", port)
    try:
        assert served != port  # taken: another, chosen by the system
        assert _get(served, "/") == (200, "text/html; charset=utf-8", b"<html>frame</html>")
        status, kind, body = _get(served, "/frame/frame.js")
        assert status == 200 and kind.startswith("text/javascript") and b"showPart" in body
        assert _get(served, "/frame/frame.css")[1].startswith("text/css")
        for shared in ("styles.css", "embed.js", "i18n.js", "textsize.js", "zoom.js"):
            assert _get(served, f"/static/{shared}")[0] == 200, shared
        assert _get(served, "/static/../live/frame.py")[0] == 404  # never outside its folders
        assert _get(served, "/static/%2e%2e/live/frame.py")[0] == 404
        assert _get(served, "/frame/frame.html")[0] == 404  # at / alone, with what it starts from
        assert _get(served, "/static/index.html")[0] == 404
        assert _get(served, "/", host="example.com")[0] == 403  # another website's name for it
    finally:
        server.shutdown()
        taken.close()


def test_the_frame_s_port_is_beside_its_engine_s() -> None:
    assert frame_module.frame_port() == desktop.ENGINE_PORT - 1


def test_the_frame_s_page_may_ask_this_and_no_more() -> None:
    tools = FrameTools(Frame(Path("serve.log"), first="build"))
    public = {n for n in dir(tools) if not n.startswith("_") and callable(getattr(tools, n))}
    assert public == {"ensure", "quit", "keep", "set_zoom"}


# -- the parts -------------------------------------------------------------------------------------


def test_osxp_s_part_starts_its_engine_behind_its_opening_page(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = home / "serve.log"
    frame = Frame(log, first="build")
    monkeypatch.setattr(desktop, "engine_here", lambda port: True)
    monkeypatch.setattr(desktop, "start_engine", lambda log: pytest.fail("started again"))
    assert frame.ensure("build") == f"http://127.0.0.1:{desktop.ENGINE_PORT}/"
    started: list[Path] = []
    monkeypatch.setattr(desktop, "engine_here", lambda port: False)

    def opening(port: int, *, log: Path, browser: Any, **kw: Any) -> bool:
        browser("http://127.0.0.1:5555/")
        return True

    monkeypatch.setattr(desktop, "open_while_starting", opening)
    monkeypatch.setattr(desktop, "start_engine", started.append)
    assert frame.ensure("build") == "http://127.0.0.1:5555/" and started == [log]
    assert frame.ensure("elsewhere") == ""


def test_live_s_part_is_set_up_in_this_program_the_welcome_once(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[tuple[int, str]] = []

    def opening(port: int, *, log: Path, browser: Any, path: str = "", **kw: Any) -> bool:
        opened.append((port, path))
        browser(f"http://127.0.0.1:6666/{path}")
        return True

    monkeypatch.setattr(desktop, "open_while_starting", opening)
    control = SimpleNamespace(in_frame=False, state="idle", closing=False, leaving_for=None)
    fronts: list[Any] = []

    def fake_set_up(port: int, *, front: Any) -> tuple:
        fronts.append(front)
        return control, SimpleNamespace(should_exit=False), None, None, None, (("its code", 0.1),)

    monkeypatch.setattr(web, "_set_up", fake_set_up)
    frame = Frame(home / "serve.log", first="live", welcome=True)
    assert frame.given()["welcome"] is True
    assert frame.ensure("live", welcome=True) == "http://127.0.0.1:6666/welcome"
    assert wait_for(lambda: web._here is not None and web._here.control is control)
    assert control.in_frame is True and opened == [(LIVE_PORT, "welcome")]
    # again (the welcome answered building, the frame let its page go): Live's page, set up once
    assert frame.ensure("live", welcome=True) == f"http://127.0.0.1:{LIVE_PORT}/"
    assert len(opened) == 1 and len(fronts) == 1 and frame.given()["welcome"] is False


# -- closing ---------------------------------------------------------------------------------------


class Streaming:
    """Live's control while a stream runs."""

    def __init__(self) -> None:
        self.state = "streaming"
        self.quits: list[bool] = []
        self.leaving_for: str | None = None
        self.closing = False

    def busy(self) -> bool:
        return self.state != "idle"

    def gone(self) -> bool:
        return self.leaving_for == "quit"

    def quit(self, *, force: bool = False) -> bool:
        self.quits.append(force)
        return True


def test_quitting_asks_once_in_the_frame_s_language_while_something_runs(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped: list[bool] = []
    closed: list[str | None] = []
    asked: list[tuple[str, str | None]] = []
    answers: list[bool] = []
    building = [False]
    monkeypatch.setattr(desktop, "_listening", lambda port: True)
    monkeypatch.setattr(desktop, "a_build_runs", lambda port, timeout_s=1.5: building[0])
    monkeypatch.setattr(desktop, "stop_the_engine", lambda port, force=False: stopped.append(force))
    monkeypatch.setattr(window_module, "to_the_front", lambda name=None: None)
    monkeypatch.setattr(window_module, "close_now", lambda name=None: closed.append(name))

    def ask(title: str, message: str, *, name: str | None = None) -> bool:
        asked.append((message, name))
        return answers.pop(0)

    monkeypatch.setattr(window_module, "ask", ask)
    frame = Frame(home / "serve.log", first="build")
    assert frame.may_quit() is True and stopped == [False] and asked == []  # nothing runs
    # a stream: asked once, in the frame's language, over its window; no, then yes
    save_prefs({"language": "fr"})
    control = Streaming()
    live = web._Starting()
    live.control = control
    web._be_here(live)
    answers[:] = [False]
    assert frame.may_quit() is False
    assert wait_for(lambda: asked) and asked[0][1] == window_module.PLACE_NAME
    assert "en marche" in asked[0][0]  # orthostudio.live.web.QUIT_WHILE_STREAMING, in French
    time.sleep(0.1)
    assert closed == [] and control.quits == []
    answers[:] = [True]
    asked.clear()
    assert frame.may_quit() is False
    assert wait_for(lambda: closed == [window_module.PLACE_NAME])
    assert control.quits == [True] and frame.said_yes is True
    assert frame.may_quit() is True  # closing comes back through here, and goes through
    # a build: its question, the build stopped on yes
    frame = Frame(home / "serve.log", first="build")
    building[0] = True
    control.state = "idle"
    closed.clear()
    answers[:] = [True]
    asked.clear()
    assert frame.may_quit() is False
    assert wait_for(lambda: closed == [window_module.PLACE_NAME])
    assert "build est en cours" in asked[0][0] and stopped[-1] is True


def test_the_frame_s_quit_closes_it_at_once_when_nothing_runs(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str | None] = []
    monkeypatch.setattr(desktop, "_listening", lambda port: False)
    monkeypatch.setattr(window_module, "close_now", lambda name=None: closed.append(name))
    frame = Frame(home / "serve.log", first="build")
    assert frame.quit() is True and closed == []  # answered first: the page's call ends
    assert wait_for(lambda: closed == [window_module.PLACE_NAME]) and frame.said_yes


def test_the_installer_s_quit_closes_the_frame(home: Path) -> None:
    frame = Frame(home / "serve.log", first="build")
    assert frame.gone() is False
    live = web._Starting()
    live.control = Streaming()
    web._be_here(live)
    assert frame.gone() is False
    live.control.leaving_for = "quit"  # the Windows installer asked Live to go (stop_running.pas)
    assert frame.gone() is True


def test_show_part_asks_the_frame_s_page(monkeypatch: pytest.MonkeyPatch) -> None:
    said: list[str] = []
    shown = SimpleNamespace(evaluate_js=said.append)
    monkeypatch.setattr(window_module, "_named", lambda name: shown)
    frame = Frame(Path("serve.log"), first="build")
    frame.show_part("live")
    frame.show_part("elsewhere")
    assert said == ['window.showPart && window.showPart("live")']


# -- a page in the frame, and the frame's own script (node) ----------------------------------------


def _node(script: str, cwd: Path) -> Any:
    if NODE is None:
        pytest.skip("node is not installed")
    out = subprocess.run(
        [NODE, "--input-type=module"],
        input=script,
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


EMBED_SCRIPT = """
import { askFrame, bindFrame, frameOrigin, zoomStep } from "./embed.js";
const out = {};
const top = {};
top.parent = top;
const make = (origin) => {
  const posted = [], listeners = {}, keys = [], style = {}, classes = [];
  const win = {
    parent: { postMessage: (m, o) => posted.push([m.type, o, m]) },
    location: { ancestorOrigins: [origin] },
    addEventListener: (type, fn) => (listeners[type] ||= []).push(fn),
    dispatchEvent: (e) => { (listeners[e.type] || []).forEach((fn) => fn(e)); return true; },
    Event: class { constructor(type) { this.type = type; } },
    document: {
      documentElement: {
        classList: { add: (c) => classes.push(c) },
        style: { setProperty: (k, v) => (style[k] = v) },
      },
      addEventListener: (type, fn) => keys.push(fn),
      querySelector: (sel) => (sel === ".statusbar" ? { offsetHeight: 31 } : null),
    },
  };
  return { win, posted, listeners, keys, style, classes };
};
out.origins = [frameOrigin(top), frameOrigin(make("https://example.com").win), frameOrigin(make("http://127.0.0.1:8640").win)];
out.steps = [
  { metaKey: true, key: "=" }, { ctrlKey: true, key: "-" }, { metaKey: true, key: "0" },
  { metaKey: true, altKey: true, key: "=" }, { key: "=" },
].map(zoomStep);
const f = make("http://127.0.0.1:8640");
const seen = [];
const page = {
  tabs: () => [{ id: "plan", label: "Plan" }], current: () => "plan",
  show: (id) => seen.push(["show", id]), prefs: (p) => seen.push(["prefs", p]),
  ownPrefs: () => ({ language: "fr" }), shown: () => seen.push(["shown"]),
  busy: () => "building",
};
const tell = bindFrame(page, f.win);
let resized = 0;
f.listeners.resize = [() => resized++];
const send = (data, origin = "http://127.0.0.1:8640", source = f.win.parent) =>
  f.listeners.message.forEach((fn) => fn({ origin, source, data }));
send({ type: "frame:show", screen: "works" });
send({ type: "frame:show", screen: "elsewhere" }, "https://example.com");
send({ type: "frame:show", screen: "elsewhere" }, "http://127.0.0.1:8640", {});
send({ type: "frame:prefs", prefs: { theme: "dark" } });
send({ type: "frame:foot", width: 240.4 });
send({ type: "frame:shown" });
f.keys.forEach((fn) => fn({ metaKey: true, key: "=", preventDefault() {} }));
tell();
out.bound = {
  classes: f.classes, posted: f.posted.map(([type, origin]) => [type, origin]), seen,
  style: f.style, resized, ready: f.posted[1][2], tabs: f.posted[2][2],
};
const outside = make("https://example.com");
out.outside = [typeof bindFrame(page, outside.win), outside.posted.length, outside.classes.length];
out.ask = [askFrame("build", make("http://127.0.0.1:8640").win), askFrame("build", top)];
process.stdout.write(JSON.stringify(out));
"""


def test_a_page_in_the_frame_leaves_it_its_bars_and_follows_it(tmp_path: Path) -> None:
    got = _node(EMBED_SCRIPT, ui_dir())
    assert got["origins"] == ["", "", "http://127.0.0.1:8640"]  # the loopback's frame alone
    assert got["steps"] == [1, -1, 0, None, None]
    bound = got["bound"]
    assert bound["classes"] == ["in-frame"]  # its bars hidden (styles.css)
    origin = "http://127.0.0.1:8640"
    assert bound["posted"] == [
        ["page:foot", origin],  # its foot's height, which the frame's foot takes
        ["page:ready", origin],
        ["page:tabs", origin],
        ["page:zoom", origin],  # Cmd+plus typed in the page: the window's zoom, the frame's
        ["page:tabs", origin],
    ]
    assert bound["ready"]["prefs"] == {"language": "fr"}  # what it kept, for a first start
    tabs = bound["tabs"]  # its screens, the one shown, and what it is doing, said beside its name
    assert tabs["tabs"] == [{"id": "plan", "label": "Plan"}] and tabs["current"] == "plan"
    assert tabs["busy"] == "building"
    assert bound["seen"] == [["show", "works"], ["prefs", {"theme": "dark"}], ["shown"]]
    assert bound["style"] == {"--frame-foot-w": "241px"} and bound["resized"] == 1
    assert got["outside"] == ["function", 0, 0]  # outside the frame nothing changes
    assert got["ask"] == [True, False]


FRAME_SCRIPT = """
import { PARTS, WORDS, stateWord, zoomKey } from "./frame/frame.js";
import { TEXT_SIZES } from "./static/textsize.js";
process.stdout.write(JSON.stringify({
  parts: PARTS,
  keys: [Object.keys(WORDS.en).sort(), Object.keys(WORDS.fr).sort()],
  words: [
    stateWord("build", "building", "fr"), stateWord("build", "", "fr"),
    stateWord("live", "streaming", "fr"), stateWord("live", "streaming", "en"),
    stateWord("live", "idle"), stateWord("live", ""), stateWord("live", "leaving", "fr"),
  ],
  zoom: [zoomKey(1), zoomKey(-1), zoomKey(0)],
  sizes: TEXT_SIZES,
}));
"""


def test_the_frame_s_own_words_and_steps(tmp_path: Path) -> None:
    # laid out as served: the frame's script under frame/, OrthoStudio XP's under static/
    shutil.copytree(FRAME_DIR, tmp_path / "frame")
    shutil.copytree(ui_dir(), tmp_path / "static", ignore=shutil.ignore_patterns("vendor"))
    got = _node(FRAME_SCRIPT, tmp_path)
    assert got["parts"] == ["build", "live"]
    assert got["keys"][0] == got["keys"][1]  # every word in both languages
    assert got["words"] == ["build en cours", "", "en vol", "in flight", "", "", "attend X-Plane"]
    assert got["zoom"] == ["=", "-", "0"]
    assert got["sizes"] == list(frame_module.TEXT_SIZES)  # what the app keeps, as the page offers


def test_the_pages_mark_what_they_leave_the_frame() -> None:
    from orthostudio.live.web import UI_DIR

    osxp = (ui_dir() / "index.html").read_text(encoding="utf-8")
    live = (UI_DIR / "index.html").read_text(encoding="utf-8")
    welcome = (UI_DIR / "welcome.html").read_text(encoding="utf-8")
    for page in (osxp, live, welcome):
        assert '<header class="topbar" data-frame-owned>' in page
    for page in (osxp, live):  # the foot's text size and zoom, the frame's in it
        assert 'id="status-zoom" hidden data-frame-owned' in page
        assert 'data-i18n-title="app.text_size" data-frame-owned' in page
    styles = (ui_dir() / "styles.css").read_text(encoding="utf-8")
    assert "html.in-frame { --topbar-h: 0px; }" in styles
    assert "html.in-frame [data-frame-owned] { display: none !important; }" in styles
    assert "padding-right: var(--frame-foot-w, 236px)" in styles
    frame = (FRAME_DIR / "frame.html").read_text(encoding="utf-8")
    assert frame_module.GIVEN in frame
    for part in ("build", "live"):
        assert f'id="part-{part}"' in frame and f'data-part="{part}"' in frame


# -- a build and a flight, each asked about while the other runs -----------------------------------

ENTRY_SCRIPT = """
import { beforeBuild } from "./osxp-entry.js";
const ask = (answer) => async () => answer;
process.stdout.write(JSON.stringify(await Promise.all([
  beforeBuild("fr", ask({ enabled: true, state: "streaming" })),
  beforeBuild("en", ask({ enabled: true, state: "starting" })),
  beforeBuild("en", ask({ enabled: true, state: "idle" })),
  beforeBuild("en", ask({ enabled: false })),
  beforeBuild("en", ask(null)),
])));
"""


def test_a_build_while_live_streams_is_asked_about_never_refused() -> None:
    """OrthoStudio XP's page asks what another part of the app has to ask before a build (app.js
    askedBeforeBuild, a hook that names nothing): Live, while it streams, that a build may slow
    X-Plane and Live down. The pilot may build all the same."""
    from orthostudio.live.web import UI_DIR

    got = _node(ENTRY_SCRIPT, UI_DIR)
    assert "peut ralentir X-Plane et Live" in got[0] and "may slow X-Plane and Live" in got[1]
    assert got[2:] == [None, None, None]
    app = (ui_dir() / "app.js").read_text(encoding="utf-8")
    build = app[app.index("async function build(install) {") :]
    assert build.index("await askedBeforeBuild()") < build.index('api("POST", "/api/jobs"')
    retry = app[app.index("async function retryJob() {") :]
    assert retry.index("await askedBeforeBuild()") < retry.index("/retry`")
    hook = app[
        app.index("async function askedBeforeBuild() {") : app.index("async function quitOsxp")
    ]
    assert "window.osxpBeforeBuild" in hook and "Live" not in hook  # it names nothing
    page = (ui_dir() / "index.html").read_text(encoding="utf-8")
    assert '<dialog id="build-question"' in page


def test_a_flight_while_osxp_builds_is_asked_about_never_refused() -> None:
    from orthostudio.live.web import UI_DIR

    js = (UI_DIR / "live.js").read_text(encoding="utf-8")
    start = js[js.index('$("start-btn").addEventListener("click"') :]
    assert start.index("await askedWhileBuilding()") < start.index('act("start"')
    prepare = js[js.index("async function prepareDeparture() {") :]
    assert prepare.index("await askedWhileBuilding()") < prepare.index('api("prepare"')
    assert 'api("osxp-build")' in js
    page = (UI_DIR / "index.html").read_text(encoding="utf-8")
    assert '<dialog id="build-running"' in page
