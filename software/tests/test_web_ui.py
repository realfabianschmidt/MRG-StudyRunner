"""Guardrail tests for the web UI conventions.

These keep three owner rules enforced without a JS test runner:
- only slide toggles (.switch), no plain square checkboxes,
- no untranslated research jargon on the participant page,
- the UI must work offline (no CDN links) and never block with alert().
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WEB = PROJECT_ROOT / "study_runner" / "frontend"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class LocaleTests(unittest.TestCase):
    def test_locale_key_sets_are_identical(self) -> None:
        en = json.loads(_read(WEB / "locales" / "en.json"))
        de = json.loads(_read(WEB / "locales" / "de.json"))

        self.assertEqual(
            sorted(en.keys()),
            sorted(de.keys()),
            "en.json and de.json must contain exactly the same translation keys",
        )


    def test_every_t_call_resolves_in_both_locales(self) -> None:
        """A missing key renders the English fallback, silently, in both languages.

        That is how "Machine settings" survived on the German settings page:
        t() succeeded, it just returned the second argument.
        """
        en = json.loads(_read(WEB / "locales" / "en.json"))
        de = json.loads(_read(WEB / "locales" / "de.json"))

        pattern = re.compile(r"""t\(\s*['"]([A-Za-z0-9_.]+)['"]""")
        sources = list((WEB / "scripts").rglob("*.js"))
        sources += list((PROJECT_ROOT / "study_runner" / "plugins").rglob("ui/*.js"))

        used: set[str] = set()
        for path in sources:
            if path.name == "i18n.js":
                continue  # its docstring example is not a real key
            used.update(pattern.findall(_read(path)))

        self.assertEqual(sorted(used - set(en)), [], "t() keys missing from en.json")
        self.assertEqual(sorted(used - set(de)), [], "t() keys missing from de.json")

    def test_german_locale_spells_umlauts(self) -> None:
        """The German UI writes oeffnen/fuer/Zurueck nowhere - it has umlauts."""
        de = json.loads(_read(WEB / "locales" / "de.json"))

        banned = (
            "fuer", "oeffn", "schliessen", "Zurueck", "zurueck", "pruef", "Pruef",
            "koennen", "waehrend", "verfuegbar", "Geraet", "Qualitaet", "naechst",
            "Naechst", "loesch", "Loesch", "laeuft", "Laeuft", "laedt", "Laedt",
        )
        offenders = [
            f"{key}: {value}"
            for key, value in de.items()
            if isinstance(value, str) and any(word in value for word in banned)
        ]
        self.assertEqual(offenders[:5], [], "write proper umlauts in de.json")


class ModuleSyntaxTests(unittest.TestCase):
    """The web scripts are ES modules and must be syntax-checked as such.

    `node --check foo.js` parses the file as CommonJS, where a top-level
    `export` is simply not looked at - it accepted `async export function`
    without complaint and the page only broke in the browser. Copying to .mjs
    is what makes node use the module grammar.
    """

    def test_every_script_parses_as_an_es_module(self) -> None:
        import shutil
        import subprocess
        import tempfile

        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")

        offenders = []
        scripts = sorted((WEB / "scripts").rglob("*.js"))
        scripts += sorted((PROJECT_ROOT / "study_runner" / "plugins").rglob("ui/*.js"))
        self.assertGreater(len(scripts), 20, "script discovery is broken")

        with tempfile.TemporaryDirectory() as work_dir:
            for path in scripts:
                copy = Path(work_dir) / f"{path.stem}.mjs"
                copy.write_text(_read(path), encoding="utf-8")
                result = subprocess.run(
                    [node, "--check", str(copy)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    first = (result.stderr or "").strip().splitlines()
                    offenders.append(f"{path.name}: {first[2] if len(first) > 2 else result.stderr[:80]}")

        self.assertEqual(offenders, [])


class ToggleTests(unittest.TestCase):
    def test_no_legacy_checkbox_row_classes_remain(self) -> None:
        offenders = []
        for path in list(WEB.rglob("*.html")) + list((WEB / "scripts").rglob("*.js")):
            text = _read(path)
            if "consent-row" in text or "checkbox-row" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [], "convert remaining rows to the .switch pattern")

    def test_every_visible_checkbox_is_a_switch_or_chip(self) -> None:
        # A checkbox is acceptable when it is the hidden input of a .switch
        # or a chips option (hidden by CSS, rendered as pills).
        pattern = re.compile(r'[^\n]*type="checkbox"[^\n]*')
        offenders = []
        for path in list((WEB / "pages").glob("*.html")) + list((WEB / "scripts").rglob("*.js")):
            for match in pattern.findall(_read(path)):
                context = match.strip()
                if "switch" in context or "chips" in context or "querySelector" in context or ".matches(" in context:
                    continue
                if "stimulus-toggle-input" in context or "pid-enabled" in context:
                    continue  # inputs inside existing .switch wrappers (multi-line markup)
                if 'name="q${i}"' in context:
                    continue  # chips option inputs of the choice card
                offenders.append(f"{path.name}: {context[:100]}")
        self.assertEqual(offenders, [])


class ParticipantLanguageTests(unittest.TestCase):
    JARGON = [
        "Visual analog scale</div>",
        "Likert scale</div>",
        "Semantic differential</div>",
        "Word Cloud</div>",
        "Free text</div>",
        "> Ranking</div>",
        "Multi-Slider</div>",
        "Mood Meter</div>",
    ]

    def test_participant_card_tags_are_localized(self) -> None:
        offenders = []
        for path in (WEB / "scripts" / "cards").glob("*.js"):
            text = _read(path)
            for phrase in self.JARGON:
                if phrase in text:
                    offenders.append(f"{path.name}: {phrase}")
        self.assertEqual(offenders, [])

    def test_study_page_never_uses_blocking_alerts(self) -> None:
        text = _read(WEB / "scripts" / "participant" / "study-controller.js")
        self.assertNotIn("alert(", text, "use showStudyNotice() instead of alert()")

    def test_participant_lifecycle_is_manifest_extension_driven(self) -> None:
        controller = _read(WEB / "scripts" / "participant" / "study-controller.js")
        camera_extension = _read(
            PROJECT_ROOT / "study_runner" / "plugins" / "camera_emotion" / "ui" / "participant.js"
        )
        camera_capture = _read(
            PROJECT_ROOT / "study_runner" / "plugins" / "camera_emotion" / "ui" / "camera-capture.js"
        )

        self.assertNotIn("camera_emotion", controller)
        self.assertNotIn("startCameraCaptureSession", controller)
        self.assertIn("loadPluginUiExtensions('participant')", controller)
        self.assertNotIn("await queueParticipantExtensionSync", controller)
        self.assertIn("participantExtensions.startStimulus", controller)
        self.assertIn("participantExtensions.beforeSubmit", controller)
        self.assertIn("plugin_status: participantExtensions.heartbeatStatus()", controller)
        self.assertIn("runParticipantAction", controller)
        self.assertIn("ingestParticipant", controller)
        self.assertIn("createParticipantExtension", camera_extension)
        self.assertIn("startCameraCaptureSession", camera_extension)
        self.assertIn("context.runParticipantAction", camera_extension)
        self.assertIn("context.ingestParticipant", camera_extension)
        self.assertNotIn("/api/study/camera-monitor/start", camera_extension)
        self.assertNotIn("/api/camera/frame", camera_capture)

    def test_generic_core_has_no_active_camera_key_branch(self) -> None:
        core_paths = (
            PROJECT_ROOT / "study_runner" / "backend" / "routes" / "helpers.py",
            PROJECT_ROOT / "study_runner" / "backend" / "routes" / "admin.py",
            PROJECT_ROOT / "study_runner" / "backend" / "routes" / "study.py",
            PROJECT_ROOT / "study_runner" / "plugin_framework" / "registry.py",
        )
        for path in core_paths:
            with self.subTest(path=path.name):
                text = _read(path)
                self.assertNotIn("camera_emotion", text)
                self.assertNotIn("CAMERA_PREVIEW", text)

        compatibility_routes = _read(
            PROJECT_ROOT / "study_runner" / "backend" / "routes" / "sensors.py"
        )
        self.assertIn("Deprecated fixed-key shim", compatibility_routes)
        self.assertIn('headers["Deprecation"] = "true"', compatibility_routes)


class EditorFieldOrderTests(unittest.TestCase):
    """The card editor reads in the order an author writes a question."""

    ORDER = ("renderPromptField", "renderInstructionField", "renderEditor", "renderNoteField", "renderEditorToggles")

    def test_open_overlay_composes_the_agreed_order(self) -> None:
        admin = _read(WEB / "scripts" / "admin" / "admin-controller.js")
        start = admin.index("editorEl.innerHTML = [")
        block = admin[start : admin.index("].join('');", start)]

        positions = [block.index(name) for name in self.ORDER]
        self.assertEqual(positions, sorted(positions), f"editor field order changed: {block}")

    def test_no_card_module_renders_its_own_prompt_field(self) -> None:
        """The prompt lives in card-info.js only.

        Nine card modules used to carry a byte-identical copy, which is how the
        shared block ended up appended *after* each card's own fields.
        """
        offenders = [
            path.name
            for path in sorted((WEB / "scripts" / "cards").glob("card-*.js"))
            if path.name != "card-info.js" and 'class="qe-prompt"' in _read(path)
        ]
        self.assertEqual(offenders, [])

    def test_editor_toggles_carry_no_inline_explanation(self) -> None:
        """No explanatory paragraph and no filled row - the title does that job."""
        info = _read(WEB / "scripts" / "cards" / "card-info.js")

        self.assertIn("editor-toggle", info)
        self.assertNotIn("switch-row", info)
        self.assertNotIn("<small", info)

        for path in sorted((WEB / "scripts" / "cards").glob("card-*.js")):
            with self.subTest(card=path.name):
                self.assertNotIn('class="switch-row"', _read(path))

    def test_editor_toggle_row_does_not_restyle_the_participant_switch(self) -> None:
        css = _read(WEB / "styles" / "main.css")

        self.assertIn(".editor-toggle {", css)
        # .switch-row still carries its filled background for the participant page.
        block = css[css.index(".switch-row {") : css.index("}", css.index(".switch-row {"))]
        self.assertIn("var(--ink-08)", block)


class PreviewModeTests(unittest.TestCase):
    """Preview looks at a study. It must never act as one."""

    def test_preview_never_sends_a_heartbeat(self) -> None:
        """The heartbeat is what claims the single tablet slot.

        If a preview sent one, opening your own study to look at it would
        block the Play button that starts it.
        """
        controller = _read(WEB / "scripts" / "participant" / "study-controller.js")
        start = controller.index("startStudyClientHeartbeat(getStudyClientHeartbeatPayload")
        guard = controller[max(0, start - 400) : start]

        self.assertIn("IS_PREVIEW", guard, "the heartbeat call must sit behind the preview guard")

    def test_preview_suppresses_every_write_in_one_place(self) -> None:
        """One barrier, not a flag checked at fifteen call sites."""
        controller = _read(WEB / "scripts" / "participant" / "study-controller.js")

        # The real senders are imported under different names and wrapped.
        self.assertIn("postJson as postJsonToServer", controller)
        self.assertIn("sendReliableStudyEvent as sendReliableStudyEventToServer", controller)
        self.assertIn("function sendStudyBeacon(", controller)

        # Nothing may reach the network around the barrier.
        for raw in ("postJsonToServer(", "sendReliableStudyEventToServer("):
            self.assertEqual(controller.count(raw), 1, f"{raw} must only be called by its wrapper")
        self.assertNotIn("navigator.sendBeacon('/api/", controller)

    def test_preview_link_and_play_button_exist(self) -> None:
        admin = _read(WEB / "pages" / "admin.html")

        self.assertIn('href="/?preview=1"', admin)
        self.assertIn('id="btn-workspace-start"', admin)
        self.assertNotIn('data-i18n-title="workspace.openStudy"', admin)

    def test_the_admin_page_has_no_browser_confirm_left(self) -> None:
        """confirm() cannot be translated, is unstyled, and freezes the page."""
        admin = _read(WEB / "scripts" / "admin" / "admin-controller.js")
        stripped = admin.replace("confirmWithModal(", "").replace("confirmAndStartFromEditor(", "")

        self.assertNotIn("confirm(", stripped)
        self.assertNotIn("alert(", stripped)
        self.assertIn("confirmWithModal", admin)

    def test_both_start_buttons_drive_the_same_flow(self) -> None:
        """One start path, two entry points - the spinner follows the button."""
        admin = _read(WEB / "scripts" / "admin" / "admin-controller.js")

        self.assertIn("buttonId = 'btn-hub-start-study'", admin)
        self.assertIn("buttonId: 'btn-workspace-start'", admin)
        self.assertIn("goToDashboard", admin)
        self.assertNotIn("const button = $('btn-hub-start-study')", admin)


class OfflineTests(unittest.TestCase):
    def test_pages_do_not_load_from_cdns(self) -> None:
        offenders = []
        for path in (WEB / "pages").glob("*.html"):
            text = _read(path)
            if "cdn." in text or "https://unpkg" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [], "vendored assets only - the lab network has no internet")


class MotionTests(unittest.TestCase):
    """The view sweep must be skippable by anyone who asks the OS to reduce motion."""

    def test_stylesheet_honours_reduced_motion(self) -> None:
        css = _read(WEB / "styles" / "main.css")

        self.assertIn(
            "@media (prefers-reduced-motion: reduce)",
            css,
            "main.css must respect the OS reduce-motion setting",
        )

    def test_reduced_motion_block_disables_the_sweep(self) -> None:
        css = _read(WEB / "styles" / "main.css")
        start = css.index("@media (prefers-reduced-motion: reduce)")
        block = css[start : css.index("\n}", css.index("{", start))]

        self.assertIn(".view-sweep", block, "the sweep overlay must be switched off, not just faded")


class SettingsShellTests(unittest.TestCase):
    """The two settings surfaces share one nav language and one shell."""

    def test_machine_settings_view_exists_with_its_mount_points(self) -> None:
        admin = _read(WEB / "pages" / "admin.html")

        for anchor in ('id="view-machine-settings"', 'id="machine-settings-nav"', 'id="machine-settings-panels"'):
            self.assertIn(anchor, admin)

    def test_every_element_id_a_controller_binds_to_exists(self) -> None:
        """Moving markup between views must not silently break a controller.

        The controllers bind with `byId(...)?.` and optional chaining, so a
        typo or a forgotten block is a no-op at runtime, not an error. This
        turns that silence into a failing test.
        """
        html_ids = set(re.findall(r'\bid="([^"]+)"', _read(WEB / "pages" / "admin.html")))
        # Rendered into the page by JS rather than present in the markup.
        generated = {"notion-study-fields"}

        missing: list[str] = []
        for path in sorted((WEB / "scripts").rglob("*.js")):
            refs = set(re.findall(r"(?:byId|\$)\('([^']+)'\)", _read(path)))
            for ref in sorted(refs - html_ids - generated):
                missing.append(f"{path.name}: {ref}")

        self.assertEqual(missing, [], "controllers reference element ids that admin.html does not define")

    def test_every_named_import_resolves_to_a_real_export(self) -> None:
        """A missing import is a runtime ReferenceError, not a load failure.

        The page keeps working until the exact moment the missing name is
        called, so this class of mistake hides until someone clicks the right
        button. Caught exactly that during the study-panel move.
        """
        scripts = WEB / "scripts"
        export_re = re.compile(r"export\s+(?:async\s+)?function\s+(\w+)|export\s+const\s+(\w+)")
        import_re = re.compile(r"import\s*\{([^}]+)\}\s*from\s*'([^']+)'")

        problems: list[str] = []
        for path in sorted(scripts.rglob("*.js")):
            for names, target in import_re.findall(_read(path)):
                if not target.startswith("."):
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    problems.append(f"{path.name}: imports from missing {target}")
                    continue
                exported = {name for pair in export_re.findall(_read(resolved)) for name in pair if name}
                for raw in names.split(","):
                    local = raw.strip().split(" as ")[0].strip()
                    if local and local not in exported:
                        problems.append(f"{path.name}: '{local}' is not exported by {target}")

        self.assertEqual(problems, [])

    def test_every_manifest_label_key_is_translated(self) -> None:
        """A missing label leaves the operator reading a raw config path.

        The settings form falls back to the dotted path when a label_key is
        absent from the locales, so the field renders as SCAN_SECONDS S rather
        than "Scan duration" - visible, but nothing fails.
        """
        import json as _json

        en = _json.loads(_read(WEB / "locales" / "en.json"))
        de = _json.loads(_read(WEB / "locales" / "de.json"))

        referenced: set[str] = set()
        for manifest_path in (PROJECT_ROOT / "study_runner" / "plugins").glob("*/manifest.json"):
            manifest = _json.loads(_read(manifest_path))
            for scope in (manifest.get("settings") or {}).values():
                if not isinstance(scope, dict):
                    continue
                for field in scope.values():
                    if isinstance(field, dict) and field.get("label_key"):
                        referenced.add(field["label_key"])

        self.assertEqual(sorted(referenced - set(en)), [], "label keys missing from en.json")
        self.assertEqual(sorted(referenced - set(de)), [], "label keys missing from de.json")

    def test_per_study_settings_are_not_reachable_from_the_machine_hub(self) -> None:
        """Study settings belong to the study, machine settings to the computer.

        A study copied to another computer must not drag that computer's setup
        with it, so the gear hub must not offer a per-study entry.
        """
        controller = _read(WEB / "scripts" / "admin" / "admin-controller.js")

        self.assertNotIn("'study-settings'", controller)
        self.assertIn('id="view-study-settings"', _read(WEB / "pages" / "admin.html"))

    def test_absorbed_views_are_gone(self) -> None:
        admin = _read(WEB / "pages" / "admin.html")

        for removed in ('id="view-nextcloud-settings"', 'id="study-settings-modal"'):
            self.assertNotIn(removed, admin, "absorbed into the study settings panel")

    def test_nav_items_reuse_the_existing_tab_styling(self) -> None:
        css = _read(WEB / "styles" / "main.css")

        # Appended to the .settings-hub-tab rules rather than a second copy,
        # so active/hover treatment can only ever be changed in one place.
        self.assertRegex(
            css,
            r"\.settings-hub-tab,\s*\n\.settings-nav-item\s*{",
            ".settings-nav-item must share the .settings-hub-tab rules",
        )


class PluginUiContractTests(unittest.TestCase):
    def test_sensor_rich_views_and_timeline_preferences_are_plugin_owned(self) -> None:
        dashboard = _read(WEB / "scripts" / "admin" / "admin-dashboard-controller.js").lower()
        timeline = _read(WEB / "scripts" / "admin" / "session-timeline.js")
        packaging = _read(
            PROJECT_ROOT.parent
            / "release_tools"
            / "pyinstaller"
            / "study_runner_server_common.py"
        )

        for sensor_key in ("brainbit", "mini_radar", "camera_emotion"):
            self.assertNotIn(sensor_key, dashboard)
        self.assertNotIn("PREFERRED_CHANNELS", timeline)
        self.assertIn("ui?.timeline?.preferred_channels", timeline)
        self.assertIn('ui.get("extensions")', packaging)
        self.assertIn('ui.get("assets")', packaging)

    def test_admin_plugin_lists_have_no_recorder_or_worker_key_maps(self) -> None:
        for name in ("admin-controller.js", "admin-dashboard-controller.js"):
            source = _read(WEB / "scripts" / "admin" / name).lower()
            self.assertNotIn("labrecorder", source)
            self.assertNotIn("emotion_worker", source)
            self.assertNotIn("plugin_tile_icons", source)
            self.assertNotIn("plugin_icons", source)

        dashboard = _read(WEB / "scripts" / "admin" / "admin-dashboard-controller.js")
        self.assertIn("status.recording_infrastructure", dashboard)
        self.assertIn("status.recording_worker", dashboard)
        self.assertIn("recording-worker-issues", dashboard)
        self.assertNotIn("pluginsWithCapability('recording_worker')", dashboard)

    def test_catalog_visibility_drives_all_generic_plugin_surfaces(self) -> None:
        catalog = _read(WEB / "scripts" / "shared" / "plugin-catalog.js")
        machine_panel = _read(WEB / "scripts" / "settings" / "machine" / "machine-settings-panel.js")
        dashboard = _read(WEB / "scripts" / "admin" / "admin-dashboard-controller.js")
        study_panel = _read(WEB / "scripts" / "settings" / "study" / "study-settings-panel.js")

        self.assertIn("PLUGIN_UI_SURFACES", catalog)
        self.assertIn("PLUGIN_UI_SURFACES.SETTINGS_HUB", machine_panel)
        self.assertIn("PLUGIN_UI_SURFACES.DASHBOARD", dashboard)
        self.assertGreaterEqual(dashboard.count("PLUGIN_UI_SURFACES.DASHBOARD"), 2)
        self.assertIn("PLUGIN_UI_SURFACES.STUDY_SETTINGS", study_panel)
        self.assertIn("PLUGIN_UI_SURFACES.DESTINATION_SETTINGS", study_panel)
        self.assertNotIn("pluginByKey('notion')", study_panel)
        self.assertNotIn("pluginByKey('nextcloud')", study_panel)
        self.assertNotIn("settingsHubAction('notion'", machine_panel)
        self.assertNotIn("settingsHubAction('nextcloud'", machine_panel)

    def test_destination_special_settings_are_reachable_without_a_core_key_list(self) -> None:
        study_panel = _read(WEB / "scripts" / "settings" / "study" / "study-settings-panel.js")
        notion = _read(WEB / "scripts" / "settings" / "study" / "notion-settings-controller.js")
        nextcloud = _read(WEB / "scripts" / "settings" / "study" / "nextcloud-settings-controller.js")

        self.assertIn("`btn-${String(pluginKey || '')}-settings`", study_panel)
        self.assertIn("data-plugin-special-settings", study_panel)
        self.assertNotIn("btn-notion-settings", study_panel)
        self.assertNotIn("btn-nextcloud-settings", study_panel)
        self.assertIn("$('btn-notion-settings')?.addEventListener", notion)
        self.assertIn("byId('btn-nextcloud-settings')?.addEventListener", nextcloud)
        self.assertIn("callbacks.openStudySettingsPanel?.('nextcloud')", nextcloud)

    def test_finalization_view_accepts_unknown_steps_and_is_accessible(self) -> None:
        view = _read(WEB / "scripts" / "admin" / "finalization-monitor-view.js")
        monitor = _read(WEB / "scripts" / "admin" / "upload-monitor.js")

        self.assertNotIn("STEP_LABELS", view)
        self.assertIn("finalizationStepLabel(step, t)", view)
        self.assertIn('role="progressbar"', view)
        self.assertIn('role="progressbar"', monitor)
        self.assertNotIn('iconoir-cloud-upload"></i>', monitor)

    def test_required_readiness_blockers_cannot_be_confirmed_away(self) -> None:
        admin = _read(WEB / "scripts" / "admin" / "admin-controller.js")
        start = admin.index("async function startLoadedStudyRun")
        blocked = admin.index("state.readiness?.start_blocked === true", start)
        warning = admin.index("state.readiness?.ready === false", blocked)
        blocked_branch = admin[blocked:warning]

        self.assertIn("showToast(message, 'error')", blocked_branch)
        self.assertIn("return;", blocked_branch)
        self.assertNotIn("confirm(", blocked_branch)

    def test_stimulus_deadline_is_fixed_before_prepare_and_stop_does_not_wait(self) -> None:
        source = _read(WEB / "scripts" / "participant" / "study-controller.js")
        start = source.index("async function startStimulusCard")
        schedule = source.index("const scheduleStartMs = performance.now()", start)
        prepare = source.index("await postJson('/api/trial/prepare'", start)

        self.assertLess(schedule, prepare)
        self.assertNotIn("await sendReliableStudyEvent('/api/stop'", source)
        self.assertIn("planned_deadline_epoch_ms: plannedDeadlineEpochMs", source[schedule:prepare + 700])


if __name__ == "__main__":
    unittest.main()
