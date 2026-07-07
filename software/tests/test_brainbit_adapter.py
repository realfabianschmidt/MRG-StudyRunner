from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.integrations.brainbit import adapter


class FakeOutlet:
    def __init__(self) -> None:
        self.samples: list[list[float]] = []

    def push_sample(self, values) -> None:
        self.samples.append(list(values))


class BrainBitAdapterTests(unittest.TestCase):
    def tearDown(self) -> None:
        adapter._lsl_outlets = {}
        adapter._routing_state["forward_to_lsl"] = False
        adapter._routing_state["forward_to_touchdesigner"] = False

    def test_lsl_mirror_is_continuous_when_outlet_exists(self) -> None:
        outlet = FakeOutlet()
        adapter._lsl_outlets = {"EEG": outlet}
        adapter._routing_state["forward_to_lsl"] = False

        adapter._mirror_line_to_lsl('EEG {"O1": 1, "O2": 2, "T3": 3, "T4": 4}')

        self.assertEqual(outlet.samples, [[1.0, 2.0, 3.0, 4.0]])


if __name__ == "__main__":
    unittest.main()
