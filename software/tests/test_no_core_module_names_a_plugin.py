"""The generic mechanisms plugins are dispatched through must stay generic.

Before Part C, "does this plugin have a secret", "can it test its connection",
"is its share-link shape valid", and "is this study ready to run" were each
decided by a hardcoded map or an if/elif naming Notion and Nextcloud directly.
A third destination plugin could not participate in any of them without a core
edit.

`tests/test_plugin_credentials_capability.py` and
`test_plugin_readiness_requirements_capability.py` already prove this
behaviourally, with a fixture plugin that only exists inside those tests. This
test pins the same guarantee statically and cheaply: the specific functions
that resolve a secret, describe where it came from, redact it, and check
readiness must not contain any real plugin's key in their own source, not
counting the module's introductory docstring where a concrete example earns
its place.

Scoped to the functions this round actually rewrote to be generic - not a
blanket sweep of the whole backend, which still has legitimate, unrelated
plugin-specific code (Notion's own status route and its one-time legacy
queue migration, sensor admin routes) that this round did not touch and does
not claim to be generic.

Widened for the 1.0 rebuild (docs/architecture-1.0-umbau.md, invariant #4 --
"no core module names a plugin key") to also cover destination-policy
resolution and plugin discovery itself: `destination_definitions_from_manifests`
and `discover_plugin_catalog` are the same shape of generic mechanism as the
five functions above, just not yet pinned.

A whole-file literal scan across every core module was considered and
rejected: it cannot tell a real branch on a plugin key from a plugin name
used as a worked example in a docstring (confirmed by hand against
`recording_contract.py` and `study_readiness_service.py`, both of which only
mention a plugin in prose), so it would need its own allowlist
infrastructure to stay honest. If that is ever needed, it belongs in its own
test built on `tests/support/import_graph.py`'s AST tooling rather than
grown ad hoc here.
"""
from __future__ import annotations

import inspect
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The six real, removable plugins as of this round - see plugins/README.md.
# lsl_markers and clock_diagnostics are deliberately absent: they are core
# recording code now, not plugins (test_plugin_removability.py).
REAL_PLUGIN_KEYS = ("brainbit", "camera_emotion", "mini_radar", "notion", "nextcloud", "osc")


def _function_source_lower(fn) -> str:
    return inspect.getsource(fn).lower()


class GenericMechanismsNameNoPluginTests(unittest.TestCase):
    def _assert_no_plugin_literal(self, fn) -> None:
        source = _function_source_lower(fn)
        hits = [key for key in REAL_PLUGIN_KEYS if key in source]
        self.assertEqual(
            hits,
            [],
            f"{fn.__module__}.{fn.__qualname__} names {hits} directly; "
            "a plugin should be reached through its manifest, not by literal key",
        )

    def test_credential_resolution_names_no_plugin(self) -> None:
        from study_runner.plugin_framework import plugin_secrets as svc

        for fn in (
            svc.secret_fields,
            svc.resolve_plugin_secret,
            svc.describe_secret_state,
            svc.describe_secret_storage_location,
            svc.list_study_credential_state,
            svc._credential_declarations,
        ):
            with self.subTest(function=fn.__name__):
                self._assert_no_plugin_literal(fn)

    def test_hardware_config_redaction_names_no_plugin(self) -> None:
        from study_runner.backend.services.settings.secrets_service import redact_hardware_config

        self._assert_no_plugin_literal(redact_hardware_config)

    def test_readiness_check_names_no_plugin(self) -> None:
        from study_runner.backend.services.studies.study_readiness_service import check_study_readiness

        self._assert_no_plugin_literal(check_study_readiness)

    def test_admin_action_dispatch_names_no_plugin(self) -> None:
        from study_runner.plugin_framework.registry import run_admin_action

        self._assert_no_plugin_literal(run_admin_action)

    def test_manifest_url_validation_names_no_plugin(self) -> None:
        """The format string is documentation; the plugin resolves itself by key."""
        from study_runner.backend.services.studies import validation

        self._assert_no_plugin_literal(validation._validate_manifest_url)

    def test_destination_policy_resolution_names_no_plugin(self) -> None:
        from study_runner.backend.services.delivery import destination_plugin_service as svc

        self._assert_no_plugin_literal(svc.destination_definitions_from_manifests)

    def test_plugin_discovery_names_no_plugin(self) -> None:
        from study_runner.plugin_framework.plugin_catalog import discover_plugin_catalog

        self._assert_no_plugin_literal(discover_plugin_catalog)


if __name__ == "__main__":
    unittest.main()
