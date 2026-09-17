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
        self.assertEqual(monitor.snapshot()['preview']['bands'], [])

    def test_battery_hysteresis_and_expiry(self):
        monitor = BrainBitMonitor()
        for value, expected in ((20, True), (23, True), (25, False), (21, False)):
            monitor.observe('BATTERY', {'percent': value}, now=100)
            self.assertEqual(monitor.snapshot(now=101)['low_battery'], expected)
        self.assertTrue(monitor.snapshot(now=221)['battery_stale'])
        self.assertFalse(monitor.snapshot(now=221)['low_battery'])

    def test_preview_is_bounded_and_retains_short_invalid_intervals(self):
        monitor = BrainBitMonitor()
        for second in range(100):
            monitor.observe('BANDS_BATCH', {'timestamps': [second], 'samples': [[0.2]],
                            'channels': ['alpha'], 'validity': 'valid'})
        self.assertEqual(len(monitor.snapshot()['preview']['bands']), 60)
        monitor.observe('BANDS_BATCH', {'timestamps': [99.1], 'samples': [[0.2]],
                        'channels': ['alpha'], 'validity': 'uncertain'})
        self.assertEqual(monitor.snapshot()['preview']['bands'][-1]['validity'], 'uncertain')

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
        with patch.object(adapter, '_lsl_epoch_offset', None), patch.object(adapter, '_lsl_local_clock', return_value=100), patch.object(adapter.time, 'time', return_value=1_800_000_000):
            first = adapter._epoch_timestamps_to_lsl([1_800_000_000])[0]
            with patch.object(adapter.time, 'time', return_value=1_900_000_000):
                second = adapter._epoch_timestamps_to_lsl([1_800_000_000.004])[0]
        self.assertAlmostEqual(second - first, .004, places=6)

    def test_recovery_clears_error_keys_and_previous_samples(self):
        with patch.object(adapter, '_monitor', BrainBitMonitor()), patch.object(adapter, '_latest_state', {'status_detail_key':'old', 'status_detail_hint_key':'old', 'bands':{'alpha':1}}), patch.object(adapter, '_config', {}):
            adapter._update_state_from_line('CONNECTING {}')
            adapter._update_state_from_line('CONNECTED {}')
            self.assertIsNone(adapter._latest_state['status_detail_key'])
            self.assertIsNone(adapter._latest_state['status_detail_hint_key'])
            self.assertIsNone(adapter._latest_state['bands'])

    def test_diagnostic_transitions_are_not_throttled(self):
        outlet = Mock()
        with patch.object(adapter, '_lsl_outlets', {'DIAGNOSTICS':outlet}), patch.object(adapter, '_lsl_local_clock', return_value=10), patch.object(adapter, '_last_diagnostic_snapshot', float('inf')):
            adapter._mirror_line_to_lsl('ARTIFACT {"both_now":1}')
            adapter._mirror_line_to_lsl('ARTIFACT {"both_now":0}')
        self.assertEqual(outlet.push_sample.call_count, 2)
        self.assertEqual([json.loads(call.args[0][0])['payload']['both_now'] for call in outlet.push_sample.call_args_list], [1,0])

    def test_windows_console_interrupt_is_not_an_access_violation(self):
        self.assertEqual(adapter._exit_reason(3221225786)['detail_key'], 'brainbit.error.consoleInterrupted')
        self.assertEqual(adapter._exit_reason(-1073741819)['detail_key'], 'brainbit.error.nativeAccessViolation')

    def test_repeated_initialize_keeps_live_eeg_outlet_and_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'cli.py'
            script.write_text('# fixture')
            with patch.object(adapter, '_config', {}), patch.object(adapter, '_process', None), patch.object(adapter, '_set_state'), patch.object(adapter, '_registered_shutdown', True), patch.object(adapter, 'start') as start, patch.object(adapter, 'stop') as stop, patch.object(adapter, '_initialize_lsl_outlets') as lsl, patch.object(adapter, '_initialize_touchdesigner_client'):
                options = dict(script_path=str(script), lsl_enabled=True)
                adapter.initialize(**options)
                outlet = object()
                with patch.object(adapter, '_process', SimpleNamespace(poll=lambda:None)), patch.object(adapter, '_lsl_outlets', {'EEG':outlet}):
                    adapter.initialize(**options)
                    self.assertIs(adapter._lsl_outlets['EEG'], outlet)
                self.assertEqual(lsl.call_count, 1)
                self.assertEqual(start.call_count, 1)
                stop.assert_not_called()

    def test_manual_contact_check_obeys_central_lock(self):
        context = SimpleNamespace(runtime_locked=True)
        with patch.object(plugin, '_restart') as restart:
            self.assertTrue(plugin._run_admin_action(context, 'check_contact', {})['study_controlled'])
            restart.assert_not_called()

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
