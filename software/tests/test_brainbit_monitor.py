from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from study_runner.plugins.sensors.brainbit import adapter, brainbit_realtime_cli as cli, plugin
from study_runner.plugins.sensors.brainbit.monitor import BrainBitMonitor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support.fake_lsl import FakePylsl  # noqa: E402


class MonitorTests(unittest.TestCase):
    def test_reconnect_clears_values_and_has_a_new_identity(self):
        monitor = BrainBitMonitor()
        monitor.observe('CONNECTING', {})
        first = monitor.connection_id
        monitor.observe('CONNECTED', {'serial': 'A'})
        monitor.observe('QUALITY', {'O1': 0.7})
        monitor.observe('WAITING', {})
        self.assertNotIn('QUALITY', monitor.snapshot()['diagnostic_state'])
        monitor.observe('CONNECTING', {})
        self.assertNotEqual(first, monitor.connection_id)
        # The graphs are the core's live view; the monitor keeps none of its own.
        self.assertNotIn('preview', monitor.snapshot())

    def test_battery_hysteresis_and_expiry(self):
        monitor = BrainBitMonitor()
        for value, expected in ((20, True), (23, True), (25, False), (21, False)):
            monitor.observe('BATTERY', {'percent': value}, now=100)
            self.assertEqual(monitor.snapshot(now=101)['low_battery'], expected)
        self.assertTrue(monitor.snapshot(now=221)['battery_stale'])
        self.assertFalse(monitor.snapshot(now=221)['low_battery'])

    def test_band_power_reaches_the_live_view_with_its_validity(self):
        lsl = FakePylsl(clock=500.0)
        adapter._streams.use_backend(lsl)
        adapter._streams.reset()
        clock = [100.2]
        original_clock = adapter._streams._live._clock
        adapter._streams._live._clock = lambda: clock[0]
        try:
            adapter._streams.open('bands', nominal_rate_hz=25.0)
            payload = {'channels': ['delta', 'theta', 'alpha', 'beta', 'gamma'], 'samples': [[0.1, 0.2, 0.3, 0.25, 0.15]],
                       'timestamps': [1_780_000_000.0], 'sample_count': 1, 'validity': 'uncertain'}
            with patch.object(adapter, '_lsl_epoch_offset', None), patch.object(adapter.time, 'time', return_value=1_780_000_000.04):
                adapter._mirror_line_to_lsl('BANDS_BATCH ' + json.dumps(payload))
            clock[0] = 100.6
            bands = adapter._streams.status_blocks()['live']['series']['bands']
            self.assertEqual(bands['channels']['alpha'][-1], 0.3)
            self.assertIs(bands['valid'][-1], False)
            # A calibration breaks the current point of the derived graphs.
            adapter._mirror_line_to_lsl('CALIB {"event": "START"}')
            clock[0] = 101.1
            self.assertIs(adapter._streams.status_blocks()['live']['series']['mental']['valid'][-1], False)
        finally:
            adapter._streams._live._clock = original_clock
            adapter._streams.close()
            adapter._streams.reset()

    def test_multiple_devices_require_selection_and_index_is_ignored(self):
        bands = [SimpleNamespace(Name='BrainBit', SerialNumber=s, Address=s) for s in ('A', 'B')]
        args = SimpleNamespace(serial_number='', device_address='', device_name='', device_index=0)
        self.assertEqual(cli._select_sensor_info(bands, args)[2], 'selection_required')
        args.serial_number = 'B'
        self.assertIs(cli._select_sensor_info(list(reversed(bands)), args)[1], bands[1])
        args.serial_number = 'missing'
        self.assertIsNone(cli._select_sensor_info(bands, args)[1])

    def test_nominal_packet_timeline_ignores_arrival_jitter_but_preserves_gaps(self):
        estimator = cli.SourceTimestampEstimator(250)
        first, _ = estimator.for_packets([1, 2], 100)
        second, events = estimator.for_packets([3, 5], 200)
        self.assertAlmostEqual(second[0] - first[-1], .004)
        self.assertAlmostEqual(second[1] - second[0], .008)
        self.assertEqual(events[-1]['gap_before'], 1)

    def test_stable_lsl_mapping_survives_wall_clock_jump(self):
        adapter._streams.use_backend(FakePylsl(clock=100.0))
        adapter._streams.open('diagnostics')
        try:
            with patch.object(adapter, '_lsl_epoch_offset', None), patch.object(adapter.time, 'time', return_value=1_800_000_000):
                first = adapter._epoch_timestamps_to_lsl([1_800_000_000])[0]
                with patch.object(adapter.time, 'time', return_value=1_900_000_000):
                    second = adapter._epoch_timestamps_to_lsl([1_800_000_000.004])[0]
        finally:
            adapter._streams.close()
        self.assertAlmostEqual(second - first, .004, places=6)

    def test_recovery_clears_error_keys_and_previous_samples(self):
        with patch.object(adapter, '_monitor', BrainBitMonitor()), patch.object(adapter, '_latest_state', {'status_detail_key':'old', 'status_detail_hint_key':'old', 'bands':{'alpha':1}}), patch.object(adapter, '_config', {}):
            adapter._update_state_from_line('CONNECTING {}')
            adapter._update_state_from_line('CONNECTED {}')
            self.assertIsNone(adapter._latest_state['status_detail_key'])
            self.assertIsNone(adapter._latest_state['status_detail_hint_key'])
            self.assertIsNone(adapter._latest_state['bands'])

    def test_diagnostic_transitions_are_not_throttled(self):
        lsl = FakePylsl(clock=10.0)
        adapter._streams.use_backend(lsl)
        adapter._streams.open('diagnostics')
        try:
            with patch.object(adapter, '_last_diagnostic_snapshot', float('inf')):
                adapter._mirror_line_to_lsl('ARTIFACT {"both_now":1}')
                adapter._mirror_line_to_lsl('ARTIFACT {"both_now":0}')
        finally:
            adapter._streams.close()
        events = [json.loads(row[0]) for row in lsl.outlet('study_runner.brainbit.diagnostics').rows]
        self.assertEqual([event['payload']['both_now'] for event in events if event['event'] == 'ARTIFACT'], [1, 0])

    def test_windows_console_interrupt_is_not_an_access_violation(self):
        self.assertEqual(adapter._exit_reason(3221225786)['detail_key'], 'brainbit.error.consoleInterrupted')
        self.assertEqual(adapter._exit_reason(-1073741819)['detail_key'], 'brainbit.error.nativeAccessViolation')

    def test_repeated_initialize_keeps_live_eeg_outlet_and_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'cli.py'
            script.write_text('# fixture')
            with patch.object(adapter, '_config', {}), patch.object(adapter, '_process', None), patch.object(adapter, '_set_state'), patch.object(adapter, '_registered_shutdown', True), patch.object(adapter, 'start') as start, patch.object(adapter, 'stop') as stop, patch.object(adapter, '_initialize_lsl_outlets') as lsl:
                options = dict(script_path=str(script), lsl_enabled=True)
                adapter.initialize(**options)
                with patch.object(adapter, '_process', SimpleNamespace(poll=lambda:None)):
                    adapter.initialize(**options)
                self.assertEqual(lsl.call_count, 1)
                self.assertEqual(start.call_count, 1)
                stop.assert_not_called()

    def test_manual_contact_check_obeys_central_lock(self):
        context = SimpleNamespace(runtime_locked=True)
        with patch.object(plugin, '_restart') as restart:
            self.assertTrue(plugin._run_admin_action(context, 'check_contact', {})['study_controlled'])
            restart.assert_not_called()

    def test_a_known_band_is_not_connected_by_switching_on(self):
        context = SimpleNamespace(
            hardware_config={'brainbit': {'enabled': True, 'device_name': 'BrainBit',
                                          'last_connected_device': {'serial_number': 'X1'}}},
            base_dir=Path('.'), data_dir=Path('.'), runtime_locked=False, study_running=False,
            resolve_project_path=lambda value: value, resolve_platform_value=lambda value: value,
        )
        with patch.object(adapter, 'initialize') as initialize, patch.object(adapter, 'wait_for_stream_contract'), \
                patch.object(plugin, '_runtime_dir', return_value='.'):
            plugin._initialize(context)
        options = initialize.call_args.kwargs
        self.assertFalse(options['start_process'])
        # The band used last time is still the target, to be offered in the list.
        self.assertEqual(options['serial_number'], 'X1')

    def test_the_auto_reconnect_switch_works_while_recording(self):
        context = SimpleNamespace(hardware_config={'brainbit': {'enabled': True}}, runtime_locked=True, study_running=True)
        with patch.object(adapter, 'is_configured', return_value=True), patch.object(adapter, 'had_connection', return_value=True), \
                patch.object(adapter, 'set_auto_reconnect') as set_auto, patch.object(plugin, '_auto_reconnect_choice', None):
            result = plugin._run_admin_action(context, 'auto_reconnect', {'enabled': False})
            self.assertFalse(result['auto_reconnect'])
            set_auto.assert_called_with(False)
            plugin._run_admin_action(context, 'auto_reconnect', {'enabled': True})
            set_auto.assert_called_with(True)

    def test_auto_reconnect_follows_the_study_run(self):
        config = {'brainbit': {'enabled': True}}
        with patch.object(adapter, 'is_configured', return_value=True), patch.object(adapter, 'had_connection', return_value=True), \
                patch.object(adapter, 'set_auto_reconnect') as set_auto, patch.object(plugin, '_auto_reconnect_choice', None):
            plugin._apply_auto_reconnect(SimpleNamespace(study_running=False), config['brainbit'])
            set_auto.assert_called_with(False)
            plugin._apply_auto_reconnect(SimpleNamespace(study_running=True), config['brainbit'])
            set_auto.assert_called_with(True)
            plugin._apply_auto_reconnect(SimpleNamespace(study_running=True), {'enabled': True, 'auto_reconnect': False})
            set_auto.assert_called_with(False)

    def test_search_returns_once_the_search_runs(self):
        from study_runner.contracts.plugin_api import PluginContext
        context = PluginContext(base_dir=Path('.'), data_dir=Path('.'), local_secrets={}, local_secrets_file=Path('s.json'),
                                hardware_config={'brainbit': {'enabled': True, 'serial_number': 'OLD', 'scan_seconds': 7}},
                                persist_hardware_config=Mock())
        with patch.object(plugin, '_restart') as restart, patch.object(adapter, 'forget_connection') as forget:
            result = plugin._run_admin_action(context, 'scan_devices', {})
        forget.assert_called_once()
        self.assertTrue(restart.call_args.kwargs['connect'])
        self.assertNotIn('serial_number', restart.call_args.args[0].hardware_config['brainbit'])
        self.assertIn('7 s', result['last_message'])

    def test_explicit_scan_never_auto_connects_even_with_one_device(self):
        args = SimpleNamespace(require_selection=True, serial_number='', device_address='', device_name='')
        self.assertEqual(cli._select_sensor_info([SimpleNamespace(SerialNumber='A')], args)[2], 'selection_required')

    def test_successful_connection_is_saved_without_replacing_explicit_target(self):
        context = SimpleNamespace(hardware_config={'brainbit': {'enabled': True, 'serial_number': 'explicit'},
                                                   'other_plugin': {'setting': 17}},
                                  base_dir=Path('.'), runtime_locked=False, persist_hardware_config=Mock())
        state = {'connection_id':'new-connection', 'status':'connected',
                 'diagnostic_state': {'CONNECTED': {'serial':'successful', 'address':'AA', 'name':'fixture'}}}
        with patch.object(adapter, 'get_status', return_value=state), patch.object(plugin, '_runtime_dir', return_value='.'), patch.object(plugin, '_read_json_file', return_value={}), patch.object(plugin, '_remembered_connection', None):
            plugin._status(context)
            plugin._status(context)
        context.persist_hardware_config.assert_called_once()
        saved = context.persist_hardware_config.call_args.args[0]
        self.assertEqual(saved['brainbit']['serial_number'], 'explicit')
        self.assertEqual(saved['brainbit']['last_connected_device']['serial_number'], 'successful')
        self.assertEqual(saved['other_plugin'], {'setting':17})
        self.assertNotIn('last_connected_device', context.hardware_config['brainbit'])

    def test_runtime_persistence_merges_with_concurrent_machine_changes(self):
        from flask import Flask
        from study_runner.apps.server.routes import helpers
        app = Flask(__name__)
        app.config.update(BASE_DIR=Path('.'), DATA_DIR=Path('.'), LOCAL_SECRETS_FILE=Path('unused.json'),
                          HARDWARE_CONFIG_FILE=Path('unused-hardware.json'))
        initial = {'brainbit': {'enabled': True}, 'other_plugin': {'value':'old'}}
        current = {'brainbit': {'enabled': True}, 'other_plugin': {'value':'changed concurrently'}}
        with app.app_context():
            context = helpers._plugin_context(initial)
        def update(_path, mutate):
            mutate(current)
            return current, None, None
        updated = json.loads(json.dumps(initial))
        updated['brainbit']['last_connected_device'] = {'serial_number':'fixture'}
        with patch.object(helpers, 'update_hardware_config', side_effect=update), patch.object(helpers, '_refresh_trial_runtime'):
            context.persist_hardware_config(updated)
        self.assertEqual(current['other_plugin']['value'], 'changed concurrently')
        self.assertEqual(current['brainbit']['last_connected_device']['serial_number'], 'fixture')
        self.assertEqual(initial['brainbit'], {'enabled':True})


if __name__ == '__main__':
    unittest.main()
