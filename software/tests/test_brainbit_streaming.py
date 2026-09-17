"""Run the acquisition callbacks with scripted SDK data, never a radio."""
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from study_runner.plugins.sensors.brainbit import adapter, brainbit_realtime_cli as cli
from study_runner.plugin_framework import driver_runtime


def acquisition_lines(*, init_failure=False, processing_failure=False, packets=(1,2,3,4)):
    captured = io.StringIO()
    sensor = SimpleNamespace(sens_family='LEBrainBit', sampling_frequency='250',
                             is_supported_feature=lambda feature:feature == 'Signal',
                             is_supported_command=lambda command:command == 'StartSignal',
                             disconnect=lambda:None)
    def command(value):
        if value == 'StartSignal':
            sensor.signalDataReceived(sensor, [SimpleNamespace(PackNum=n, Marker=0,
                O1=1e-6,T3=3e-6,T4=4e-6,O2=2e-6) for n in packets])
    sensor.exec_command = command
    info = SimpleNamespace(Name='fixture', SerialNumber='fixture-serial', Address='AA')
    scanner = SimpleNamespace(start=lambda:None, stop=lambda:None, sensors=lambda:[info], create_sensor=lambda _:sensor)
    math_instance = Mock()
    math_instance.is_both_sides_artifacted.return_value = False
    math_instance.is_artifacted_sequence.return_value = False
    math_instance.get_calibration_percents.return_value = 100
    math_instance.calibration_finished.return_value = True
    math_instance.read_spectral_data_percents_arr.return_value = [SimpleNamespace(delta=10,theta=20,alpha=30,beta=25,gamma=15)]
    math_instance.read_mental_data_arr.return_value = [SimpleNamespace(inst_attention=50,inst_relaxation=50,rel_attention=60,rel_relaxation=40)]
    if processing_failure:
        math_instance.process_data_arr.side_effect = RuntimeError('fixture math failure')
    factory = Mock(return_value=math_instance, side_effect=RuntimeError('fixture init failure') if init_failure else None)
    globals_ = {
        'Scanner':lambda _:scanner, 'SensorFamily':SimpleNamespace(LEBrainBit=1),
        'SensorFeature':SimpleNamespace(Signal='Signal',Resist='Resist',FPG='FPG',MEMS='MEMS'),
        'SensorCommand':SimpleNamespace(StartSignal='StartSignal',StopSignal='StopSignal'),
        'lib_settings':SimpleNamespace(MathLibSetting=lambda **kw:kw, ArtifactDetectSetting=lambda **kw:kw,
                                       MentalAndSpectralSetting=lambda **kw:kw),
        'emotional_math':SimpleNamespace(EmotionalMath=factory),
        'support_classes':SimpleNamespace(RawChannels=lambda left,right:(left,right)),
    }
    with ExitStack() as stack:
        for name, value in globals_.items():
            stack.enter_context(patch.object(cli,name,value,create=True))
        for name in ('_ensure_requirements','_load_sdk_modules','_validate_sdk_api_surface'):
            stack.enter_context(patch.object(cli,name))
        stack.enter_context(redirect_stdout(captured))
        result = cli.main(['--no-osc','--serial-number','fixture-serial','--resist-seconds','0',
                           '--signal-seconds','1','--max-session-attempts','1'])
    assert result == 0, captured.getvalue()
    lines = [line for line in captured.getvalue().splitlines() if ' {' in line]
    return [(line.split(' ',1)[0],json.loads(line.split(' ',1)[1])) for line in lines]


class StreamingTests(unittest.TestCase):
    def test_optional_math_failures_keep_all_raw_samples_and_scaling(self):
        for setting in ('init_failure','processing_failure'):
            with self.subTest(setting=setting):
                lines = acquisition_lines(**{setting:True})
                self.assertTrue(any(tag == 'EMO_INIT_FAIL' for tag,_ in lines))
                rows = [row for tag,payload in lines if tag == 'EEG_BATCH' for row in payload['samples']]
                self.assertEqual(rows, [[1.,2.,3.,4.]]*4)
                self.assertFalse(any(tag in {'BANDS_BATCH','MENTAL_BATCH','CALLBACK_ERROR'} for tag,_ in lines))

    def test_discontinuity_resets_math_without_discarding_raw_frames(self):
        lines = acquisition_lines(packets=(1,2,4,5))
        self.assertTrue(any(tag == 'CALIB' and value.get('event') == 'RESET' for tag,value in lines))
        self.assertEqual(sum(len(value['samples']) for tag,value in lines if tag == 'EEG_BATCH'), 4)
        self.assertFalse(any(tag == 'MENTAL_BATCH' for tag,_ in lines))

    def test_sdk_values_and_timestamps_survive_adapter_publication(self):
        lines = acquisition_lines()
        outlet = Mock()
        with patch.object(adapter, '_lsl_outlets', {'EEG':outlet}), patch.object(adapter, '_lsl_epoch_offset', -1_700_000_000), patch.object(adapter, '_lsl_local_clock', return_value=1), patch.object(adapter, '_eeg_lsl_channels', cli.EEG_CHANNELS), patch.object(adapter, '_set_state'):
            for tag,payload in lines:
                if tag == 'EEG_BATCH':
                    adapter._mirror_line_to_lsl(tag + ' ' + json.dumps(payload))
        rows = [row for call in outlet.push_chunk.call_args_list for row in call.args[0]]
        self.assertEqual(rows, [[1.,2.,3.,4.]] * 4)
        stamps = [ts for call in outlet.push_chunk.call_args_list for ts in call.args[1]]
        self.assertAlmostEqual(stamps[-1] - stamps[0], .012, places=6)
        self.assertTrue(any(tag == 'BANDS_BATCH' and value['validity'] == 'valid' for tag,value in lines))

    def test_plugin_prints_cannot_corrupt_protocol_frames(self):
        protocol, diagnostic = io.StringIO(), io.StringIO()
        def noisy_plugin(_):
            # A print without a newline used to swallow the following response.
            print('SDK status without newline', end='')
            driver_runtime._emit_response('fixture',ok=True,result={'value':123})
            print('continuation')
            return 0
        with patch.object(driver_runtime,'_serve_plugin_driver',side_effect=noisy_plugin), patch.object(sys,'stdout',protocol), patch.object(sys,'stderr',diagnostic):
            self.assertEqual(driver_runtime.run_plugin_driver('fixture'),0)
        self.assertEqual(len(protocol.getvalue().splitlines()),1)
        response = json.loads(protocol.getvalue().split(' ',1)[1])
        self.assertEqual(response['result'],{'value':123})
        self.assertIn('SDK status without newlinecontinuation',diagnostic.getvalue())

    @unittest.skipUnless(os.environ.get('STUDY_RUNNER_XDF_CORE_TEST'), 'native core path not provided')
    def test_sdk_adapter_native_xdf_round_trip(self):
        import pyxdf
        from study_runner.data_core.worker.core import NativeXdfCore
        core = NativeXdfCore(Path(os.environ['STUDY_RUNNER_XDF_CORE_TEST']))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'brainbit-fixture.xdf'
            writer = core.create_writer(path)
            channels = ''.join(f'<channel><label>{name}</label><unit>microvolt</unit></channel>' for name in cli.EEG_CHANNELS)
            header = ('<info><name>BrainBit_EEG</name><type>EEG</type><channel_count>4</channel_count>'
                      '<nominal_srate>250</nominal_srate><channel_format>float32</channel_format>'
                      '<source_id>study_runner.brainbit.eeg</source_id><desc><channels>' + channels + '</channels></desc></info>')
            writer.write_stream_header(1, header)
            writer.write_stream_header(2, '<info><name>BrainBit_DIAGNOSTICS</name><type>DIAGNOSTICS</type><channel_count>1</channel_count><nominal_srate>0</nominal_srate><channel_format>string</channel_format><source_id>study_runner.brainbit.diagnostics</source_id></info>')
            class RawOutlet:
                def push_chunk(self, values, timestamps):
                    writer.write_samples(1,timestamps,values,channel_format='float32',channel_count=4)
            class DiagnosticOutlet:
                def push_sample(self, values, timestamp):
                    writer.write_samples(2,[timestamp],[values],channel_format='string',channel_count=1)
            try:
                with patch.object(adapter,'_lsl_outlets',{'EEG':RawOutlet(),'DIAGNOSTICS':DiagnosticOutlet()}), patch.object(adapter,'_lsl_epoch_offset',-1_700_000_000), patch.object(adapter,'_lsl_local_clock',side_effect=range(100,10000)), patch.object(adapter,'_eeg_lsl_channels',cli.EEG_CHANNELS), patch.object(adapter,'_set_state'):
                    for tag,payload in acquisition_lines():
                        if tag in {'EEG_BATCH','EMO_INIT','CALIB','ARTIFACT'}:
                            adapter._mirror_line_to_lsl(tag + ' ' + json.dumps(payload))
                writer.write_stream_footer(1, '<info><sample_count>4</sample_count></info>')
                writer.write_stream_footer(2, '<info></info>')
                writer.close(durable=True)
            finally:
                writer.destroy()
            streams,_ = pyxdf.load_xdf(str(path),synchronize_clocks=False,dejitter_timestamps=False)
            eeg = next(stream for stream in streams if stream['info']['type'] == ['EEG'])
            self.assertEqual(eeg['time_series'].tolist(), [[1.,2.,3.,4.]]*4)
            recorded_channels = eeg['info']['desc'][0]['channels'][0]['channel']
            self.assertEqual([channel['label'][0] for channel in recorded_channels], list(cli.EEG_CHANNELS))
            self.assertAlmostEqual(eeg['time_stamps'][-1]-eeg['time_stamps'][0],.012,places=6)
            diagnostic = next(stream for stream in streams if stream['info']['type'] == ['DIAGNOSTICS'])
            events = [json.loads(row[0])['event'] for row in diagnostic['time_series']]
            self.assertIn('CALIB',events)
            self.assertIn('ARTIFACT',events)
