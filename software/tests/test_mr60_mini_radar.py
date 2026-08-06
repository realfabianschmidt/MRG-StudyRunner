from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugins.mr60_mini_radar import adapter


class MR60BleDecoderTests(unittest.TestCase):
    def setUp(self) -> None:
        adapter._reset_ble_stats()

    def test_decode_ble_packet_scales_values_and_flags(self) -> None:
        packet = adapter.BLE_PACKET.pack(
            1,
            0x07,
            42,
            123456,
            721,
            134,
            1825,
            120,
            -240,
            12,
        )

        sample = adapter._decode_ble_packet(packet)

        self.assertIsNotNone(sample)
        self.assertEqual(sample["sequence_number"], 42)
        self.assertTrue(sample["valid"])
        self.assertTrue(sample["stabilized"])
        self.assertTrue(sample["present"])
        self.assertEqual(sample["heartRate"], 72.1)
        self.assertEqual(sample["breathRate"], 13.4)
        self.assertEqual(sample["distance"], 182.5)
        self.assertEqual(sample["heartPhase"], 1.2)
        self.assertEqual(sample["breathPhase"], -2.4)

    def test_decode_ble_packet_tracks_missing_values_and_sequence_gaps(self) -> None:
        first = adapter.BLE_PACKET.pack(
            1,
            0x01,
            10,
            1000,
            adapter.MISSING_INT16,
            120,
            adapter.MISSING_INT16,
            adapter.MISSING_INT16,
            adapter.MISSING_INT16,
            adapter.MISSING_INT16,
        )
        second = adapter.BLE_PACKET.pack(
            1,
            0x01,
            13,
            1300,
            800,
            130,
            1000,
            0,
            0,
            0,
        )

        first_sample = adapter._decode_ble_packet(first)
        second_sample = adapter._decode_ble_packet(second)

        self.assertIsNone(first_sample["heartRate"])
        self.assertIsNone(first_sample["distance"])
        self.assertEqual(second_sample["dropped_since_previous"], 2)
        self.assertEqual(second_sample["total_dropped"], 2)
        self.assertEqual(second_sample["device_interval_ms"], 300)


if __name__ == "__main__":
    unittest.main()
