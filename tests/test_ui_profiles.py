# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The settings profiles in the page (``docs/specs/ui.md`` 2.4 and 2.1): the list at the top of
Settings and in the Plan's step 1 from the second profile on, Save as a new profile in Save's bar,
the profile's name in Works. The name check runs under ``node``."""

from __future__ import annotations

from orthostudio.config.profiles import COMPUTER_SETTINGS, MAX_NAME
from test_ui_static import INDEX_FILE, UI, _css_rules, _function_body, _node_json, _node_mock


def test_the_page_shares_the_computers_settings_with_the_engine() -> None:
    got = _node_json("app.js", "m.COMPUTER_SETTINGS")
    assert tuple(got) == COMPUTER_SETTINGS
    assert _node_json("settings.js", "m.PROFILE_NAME_MAX") == MAX_NAME
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    assert f'id="settings-new-name" class="settings-new-input" maxlength="{MAX_NAME}"' in html


def test_a_name_is_checked_before_anything_is_sent() -> None:
    got = _node_json(
        "settings.js",
        """[m.profileNameProblem("  ", []), m.profileNameProblem("alpes", ["Main", "Alpes"]),
           m.profileNameProblem(" Alpes ", ["Main"]), m.profileNameProblem(null, [])]""",
    )
    assert got[0] == "Give the profile a name."
    assert got[1] == "A profile is already called “Alpes”."  # the one that has it, as written
    assert got[2] is None and got[3] == got[0]


def test_the_lists_show_from_the_second_profile_and_wait_for_unsaved_changes() -> None:
    """A computer with one set of settings sees nothing new but *Save as a new profile*; with two,
    the list stands at the top of Settings and above step 1's source, and waits while changes are
    not saved: changing profile then would lose them without a word."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "return Boolean(state.profiles?.named);" in _function_body(app_js, "profilesShown")
    plan = _function_body(app_js, "renderPlanProfile")
    assert (
        "field.hidden = !shown;" in plan and "select.disabled = dirty || state.profileBusy;" in plan
    )
    top = _function_body(app_js, "renderSettingsProfile")
    assert "disabled: dirty || busy" in top and 't("settings.profile_unsaved")' in top
    assert "renderProfiles();" in _function_body(app_js, "renderUnsaved")
    assert "settingsUnsaved()" in _function_body(app_js, "switchProfile")
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    step1 = html[html.index('id="plan-profile"') :]
    assert step1.index('id="plan-profile-select"') < step1.index('id="provider-select"')
    form = html[html.index('id="settings-form"') :]
    assert form.index('id="settings-profile"') < form.index('id="settings-search"')
    # Save names the profile it writes into; its bar asks a new profile's name
    assert 't("settings.save_in", { name })' in _function_body(app_js, "renderSaveButtons")
    bar = html[html.index('<div class="actions settings-actions">') :]
    bar = bar[: bar.index("</form>")]
    for part in ('id="settings-save-new"', 'id="settings-new"', 'id="settings-new-create"'):
        assert part in bar, part


def test_choosing_a_profile_gives_step_1_its_source_and_level() -> None:
    """The list stands above them and says so; a plain Save leaves them as the pilot set them."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    take = _function_body(app_js, "planTakesProfile")
    assert "state.planZl = Number(essential.zoom_level);" in take
    assert 'renderProviders(essential.provider || "BI");' in take
    for name in ("switchProfile", "createProfile", "deleteProfile"):
        assert "planTakesProfile();" in _function_body(app_js, name), name
    assert "planTakesProfile" not in _function_body(app_js, "saveSettings")


def test_enter_in_a_name_field_does_not_save_the_settings() -> None:
    """Both name fields are inside the settings form, whose Enter is Save."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    top = _function_body(app_js, "renderSettingsProfile")
    assert 'if (ev.key === "Enter") {\n        ev.preventDefault();\n        renameProfile(' in top
    assert "ev.preventDefault();\n      createProfile();" in _function_body(app_js, "boot")


def test_works_names_the_profile_of_a_build() -> None:
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert 'job.profile ? t("works.profile", { name: job.profile }) : ""' in app_js
    assert "profile: d.profile ?? null" in _function_body(app_js, "mockJobSummary")


def test_the_profile_line_keeps_the_stylesheets_parts() -> None:
    rules = dict(_css_rules((UI / "styles.css").read_text(encoding="utf-8")))
    row = rules[".settings-profile-row"]
    assert "gap: 10px;" in row and "flex-wrap: wrap;" in row
    assert "var(--section-gap)" in rules[".settings-profile"]
    assert "var(--fs-title-3)" in rules[".settings-profile-label"]
    assert "width: 240px;" in rules[".settings-new input.settings-new-input"]


def test_step_1_says_when_its_source_or_level_is_no_longer_the_profiles() -> None:
    """A Save in Settings never changes step 1 (a user, 2026-09-20), so after one the Plan could
    name a profile whose source and level it no longer shows: a line says what the profile holds,
    with *Use them* (2026-11-02). Its level is capped as step 1 caps it, so that the line goes once
    the button is pressed."""
    providers = '[{code: "BI", max_zl: 19}, {code: "Arc", max_zl: 19}, {code: "NL", max_zl: 18}]'
    cases = [
        ('"Arc", zoom_level: 18', '"Arc", zl: 17'),
        ('"BI", zoom_level: 16', '"Arc", zl: 16'),
        ('"BI", zoom_level: 16', '"BI", zl: 16'),
        ('"NL", zoom_level: 19', '"NL", zl: 18'),
        ('"Gone", zoom_level: 16', '"BI", zl: 16'),
    ]
    calls = ", ".join(
        f"m.profileDiffers({{provider: {held}}}, {{provider: {shown}}}, {providers})"
        for held, shown in cases
    )
    got = _node_json("settings.js", f"[{calls}]")
    assert got[0] == {"source": {"code": "Arc", "max_zl": 19}, "zl": 18,
                      "sourceDiffers": False, "zlDiffers": True}  # fmt: skip
    assert got[1]["source"]["code"] == "BI" and got[1]["zl"] == 16
    # the line names only what still differs: the right source chosen by hand, the level alone
    words = _node_json(
        "settings.js",
        """[[false, true], [true, false], [true, true]].map(([s, z]) => m.profileDiffersText(
             "Alpes", {zl: 18, sourceDiffers: s, zlDiffers: z}, "Esri"))""",
    )
    assert words == ["The profile “Alpes” is set to ZL18.", "The profile “Alpes” is set to Esri.",
                     "The profile “Alpes” is set to Esri, ZL18."]  # fmt: skip
    assert got[2] is None and got[3] is None and got[4] is None
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    plan = _function_body(app_js, "renderPlanProfile")
    assert '$("plan-profile-differs").hidden = !differs;' in plan
    assert "profileDiffersText(state.profiles.current, differs," in plan
    # drawn again whenever step 1's source or level changes, and after a save
    assert "renderPlanProfile();" in _function_body(app_js, "renderZlOptions")
    assert "renderProfiles();" in _function_body(app_js, "renderUnsaved")
    assert "planTakesProfile();" in app_js[app_js.index('$("plan-profile-take")') :][:200]
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    field = html[html.index('id="plan-profile"') : html.index('id="provider-select"')]
    assert 'id="plan-profile-differs"' in field and 'id="plan-profile-take"' in field
    rules = dict(_css_rules((UI / "styles.css").read_text(encoding="utf-8")))
    assert "color: var(--warn);" in rules[".plan-profile-differs > .help"]


def test_save_names_its_profile_and_reads_the_list_again_when_another_is_current() -> None:
    """Another page of the same engine made another profile current: Save wrote this page's answers
    into it, its own settings lost without a word (a campaign, 2026-11-04). The page now says
    which profile it saves into, and on CFG_PROFILE_CHANGED reads the list again so that Save
    names the one current now; the mock refuses as the engine does."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    save = _function_body(app_js, "saveSettings")
    assert 'encodeURIComponent(state.profiles.current || "")' in save
    assert 'api("PUT", `/api/settings${profile}`, state.settingsDraft)' in save
    refused = save[save.index('"CFG_PROFILE_CHANGED"') :]
    steps = ("await loadProfiles();", 'state.settings = await api("GET", "/api/settings");',
             "renderSettings();", "showSettingsError(e);")  # fmt: skip
    assert [refused.index(step) for step in steps] == sorted(refused.index(s) for s in steps)
    # the answers on screen stay: Undo my changes goes back to the profile current now
    assert "setDraft" not in refused[: refused.index("showSettingsError(e);")]
    words = (UI / "i18n.js").read_text(encoding="utf-8")
    assert words.count("CFG_PROFILE_CHANGED: [") == 2  # the page's words, French and English
    assert "maintenant « {name} »" in words and "the one in use is now “{name}”" in words
    got = _node_mock(
        """
        const saved = (await call("GET", "/api/settings")).ok;
        await call("POST", "/api/profiles", {name: "Alpes", settings: saved, first_name: "Main"});
        const stale = await call("PUT", "/api/settings?profile=Main", saved);
        const fresh = await call("PUT", "/api/settings?profile=Alpes", saved);
        const older = await call("PUT", "/api/settings", saved);
        process.stdout.write(JSON.stringify({stale, fresh: Boolean(fresh.ok),
                                             older: Boolean(older.ok)}));
        """
    )
    assert got == {"stale": {"status": 409, "code": "CFG_PROFILE_CHANGED"}, "fresh": True,
                   "older": True}  # fmt: skip
