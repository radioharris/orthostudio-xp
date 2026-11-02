# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The settings profiles in the page (``docs/specs/ui.md`` 2.4 and 2.1): the list at the top of
Settings and in the Plan's step 1 from the second profile on, Save as a new profile in Save's bar,
the profile's name in Works. The name check runs under ``node``."""

from __future__ import annotations

from orthostudio.config.profiles import COMPUTER_SETTINGS, MAX_NAME
from test_ui_static import INDEX_FILE, UI, _css_rules, _function_body, _node_json


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
    assert got[1] == "A profile is already called “alpes”."
    assert got[2] is None and got[3] == got[0]


def test_the_lists_show_from_the_second_profile_and_wait_for_unsaved_changes() -> None:
    """A computer with one set of settings sees nothing new but *Save as a new profile*; with two,
    the list stands at the top of Settings and above step 1's source, and waits while changes are
    not saved: changing profile then would lose them without a word."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "return profileNames().length > 1;" in _function_body(app_js, "profilesShown")
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
